import os
from typing import Any

from mkdb.db import mkdb
from mkdb.config.db import store_config as _store_config
from mkdb.db.query_workers import QueryDispatcher
from mkdb.db.storage.log_manager import LogManager
from mkdb.db.storage.index_manager import IndexManager
from mkdb.db.storage import blob_store
from mkdb.db.storage import serializer as _serializer
from mkdb.db.cache.ram_cache import RamCache
from mkdb.db.cache.write_queue import WriteQueue
from mkdb.db.query.query_engine import QueryEngine, QuerySyntaxError
from mkdb.db.maintenance.compactor import Compactor
from mkdb.db.maintenance.task_scheduler import TaskScheduler
from mkdb.db.parity.parity_manager import ParityManager


class store:
    def __init__(self, db: mkdb, store_config: _store_config):
        self.db = db
        self.config = store_config
        self._dispatcher: QueryDispatcher | None = None
        self.log_manager: LogManager | None = None
        self.index_manager: IndexManager | None = None
        self._ram_cache: RamCache | None = None
        self._write_queue: WriteQueue | None = None
        self.query_engine: QueryEngine | None = None
        self._compactor: Compactor | None = None
        self._task_scheduler: TaskScheduler | None = None
        self._parity_manager: ParityManager | None = None

    @property
    def store_path(self):
        return os.path.join(self.db.file_path, "stores", self.config.name)

    @property
    def dispatcher(self) -> QueryDispatcher:
        """Lazy-initialised query dispatcher for this store."""
        if self._dispatcher is None:
            self._dispatcher = QueryDispatcher(
                store_name=self.config.name,
                base_path=self.store_path,
                config=self.config.query_worker_config,
                store_config=self.config
            )
            self._dispatcher.start()
        return self._dispatcher

    def setup(self):
        """Setup the store based on the configuration."""
        os.makedirs(self.store_path, exist_ok=True)
        seg_threshold = getattr(self.config.file_config, "segment_threshold",
                                self.config.file_config.file_size_split_trigger)
        self.log_manager = LogManager(
            store_path=self.store_path,
            service=self.config.name,
            segment_threshold=seg_threshold,
        )
        self.index_manager = IndexManager(
            store_path=self.store_path,
            service=self.config.name,
        )
        print(f"Store '{self.config.name}' initialized at {self.store_path}")
        from mkdb.server.event_log import emit as _emit
        _emit("info", f"store:{self.config.name}", f"Store loaded from {self.store_path}")

        # Parity manager — boot verification runs at startup
        nsym = getattr(self.config.file_config, "parity_nsym", 10)
        self._parity_manager = ParityManager(
            store_path=self.store_path,
            service=self.config.name,
            nsym=nsym,
        )
        self._parity_manager.boot_verify_all(self.log_manager)

        # RAM cache
        self._ram_cache = RamCache(
            max_size=self.config.ram_config.max_size,
            ttl=self.config.ram_config.ttl,
            clean_type=self.config.ram_config.clean_type,
        )

        # Write queue
        debounce = getattr(self.config, "write_queue_config",
                           None)
        debounce_window = debounce.debounce_window if debounce else 5.0
        max_pending     = debounce.max_pending     if debounce else 10_000
        self._write_queue = WriteQueue(
            debounce_window=debounce_window,
            max_pending=max_pending,
        )

        def _handle_write(task: dict) -> None:
            """Flush a debounced write task to the log/index layer."""
            rid   = task["record_id"]
            delta = task["delta"]
            # Capture the active segment before write so we can detect rollover
            seg_before = self.log_manager.active_segment if self.log_manager else None
            # Call the low-level write (defined by WS-1); it writes to log + index
            meta = self._write_to_storage(rid, delta)
            if self.query_engine is not None:
                self.query_engine.on_write(rid, delta)
            self.invalidate_cache(rid, metadata=meta)
            # On segment rollover: seal the old segment with parity + flush indexes
            if self.log_manager and seg_before is not None and self.log_manager.active_segment != seg_before:
                if self.query_engine is not None:
                    self.query_engine.save_all()
                if self._parity_manager is not None:
                    try:
                        self._parity_manager.generate(seg_before)
                    except Exception:
                        pass  # parity failure must never block writes

        self._write_queue.register_handler("write", _handle_write)
        self._write_queue.start()

        # Query engine
        self.query_engine = QueryEngine(self)
        self.query_engine.build_indexes()

        # Compaction
        self._compactor = Compactor(self)

        # Register compact handler on write queue
        def _handle_compact(task: dict) -> None:
            seq_int = task.get("delta", {}).get("segment")
            if seq_int is not None:
                self._compactor.compact_segment(int(seq_int))
                if self._parity_manager is not None:
                    try:
                        self._parity_manager.generate(int(seq_int))
                    except ImportError:
                        pass   # reedsolo not installed; silently skip

        self._write_queue.register_handler("compact", _handle_compact)

        # Task scheduler
        self._task_scheduler = TaskScheduler(self)
        self._task_scheduler.start()

    def query(self, operation: str, params: dict, timeout: float | None = None):
        """
        Submit a query task to the worker pool and return the result.

        Parameters
        ----------
        operation : str   "read" | "query" | "count" | "exists" | "multi_read"
        params    : dict  Operation-specific parameters.
        timeout   : float Optional per-call timeout override.
        """
        return self.dispatcher.submit(operation, params, timeout=timeout)

    def invalidate_cache(self, record_id: str, metadata: Any = None) -> None:
        """
        Broadcast a cache-invalidation for record_id to all query workers.
        Called by the write queue after a record is flushed to disk.
        """
        if self._dispatcher is not None:
            self._dispatcher.invalidate(record_id, metadata=metadata)

    def stop(self) -> None:
        """Gracefully stop the query worker pool for this store."""
        if self._dispatcher is not None:
            self._dispatcher.stop()
            self._dispatcher = None

    def generate_id(self) -> str:
        """Generate a unique record ID using this store's entity_config.

        Samples random characters from ``entity_config.token_chars`` at
        ``entity_config.token_length``.  Collision-checks against the live
        index.  If every attempt at the current length fails AND
        ``entity_config.auto_expand`` is True, the token_length is incremented
        by 1, the config is saved, and generation retries at the new length.
        """
        import random
        ec = self.config.entity_config
        chars = ec.token_chars or "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        max_attempts_per_length = 200

        while True:
            length = ec.token_length
            for _ in range(max_attempts_per_length):
                candidate = "".join(random.choices(chars, k=length))
                # Accept immediately if the index says it doesn't exist
                if self.index_manager is None or self.index_manager.get(candidate) is None:
                    return candidate

            # Exhausted attempts — keyspace is likely saturated at this length
            if ec.auto_expand:
                ec.token_length += 1
                self.db.config.save()
                # Loop again with the expanded length
            else:
                raise RuntimeError(
                    f"Could not generate a unique ID for store '{self.config.name}' "
                    f"after {max_attempts_per_length} attempts at token_length={length}. "
                    "Enable auto_expand in the store's entity config, or increase token_length."
                )

    def write(self, record_id: str, delta: dict) -> None:
        """
        Write a delta update for a record. Updates cache immediately by merging, 
        flushes to disk via the write queue.
        """
        # Ensure we have the current state merged in cache to support future reads
        if self._ram_cache is not None:
            nested = getattr(self.config, "nested_queries_enabled", False)
            self._ram_cache.apply_delta(record_id, delta, nested_enabled=nested)
        
        if self._write_queue is not None:
            self._write_queue.enqueue({
                "op":        "write",
                "store":     self.config.name,
                "record_id": record_id,
                "delta":     delta,
                "ts":        __import__("time").time(),
            })
        else:
            # Fallback: direct write if queue not initialised
            self._write_to_storage(record_id, delta)

    def _write_to_storage(self, record_id: str, delta: dict) -> None:
        """
        Lowest-level write. Reads existing record from disk, applies delta, 
        and appends the new version to the log.
        """
        if self.log_manager is None or self.index_manager is None:
            raise RuntimeError("Store is not set up. Call setup() first.")
        
        # 1. Fetch existing data from disk (bypass cache to get actual storage state)
        # Note: we use our existing read() logic but we strip out the cache check for safety
        existing_data = {}
        entry = self.index_manager.get(record_id)
        if entry:
            seg, offset, size = entry
            from mkdb.db.storage import serializer as _serializer
            try:
                line_str = self.log_manager.read(seg, offset, size)
                _, existing_data = _serializer.deserialize_record(line_str)
            except Exception:
                existing_data = {}

        # 2. Merge delta (with optional deep nesting support)
        from mkdb.objects import partition_object, deep_update
        nested_enabled = getattr(self.config, "nested_queries_enabled", False)
        if nested_enabled:
            full_record = deep_update(existing_data, partition_object(delta))
        else:
            full_record = {**existing_data, **delta}

        # 3. Serialize and append
        blob_threshold = getattr(self.config.file_config, "blob_threshold", 5 * 1024 * 1024)
        from mkdb.db.storage import serializer as _serializer
        from mkdb.db.storage import blob_store
        
        line_str = _serializer.serialize_record(record_id, full_record)
        if len(line_str.encode("utf-8")) > blob_threshold:
            seg, offset, size = blob_store.write_blob(self.store_path, record_id, line_str)
        else:
            seg, offset, size = self.log_manager.append(record_id, line_str)
        
        # 4. Update index to point to the new full version
        self.index_manager.set(record_id, seg, offset, size)
        return (seg, offset, size)

    def read(self, record_id: str) -> dict | None:
        """Read a record — checks RAM cache first, falls back to disk."""
        if self._ram_cache is not None:
            cached = self._ram_cache.get(record_id)
            if cached is not None:
                return {"_id": record_id, **cached}
        if self.index_manager is None:
            raise RuntimeError("Store is not set up. Call setup() first.")
        entry = self.index_manager.get(record_id)
        if entry is None:
            return None
        seg, offset, size = entry
        if self.log_manager is None:
            raise RuntimeError("Store is not set up. Call setup() first.")
        from mkdb.db.storage import serializer as _serializer
        line_str = self.log_manager.read(seg, offset, size)
        _, flat_dict = _serializer.deserialize_record(line_str)
        if self._ram_cache is not None:
            self._ram_cache.set(record_id, flat_dict)
        return {"_id": record_id, **flat_dict}

    def delete(self, record_id: str) -> None:
        """Soft-delete a record."""
        if self._ram_cache is not None:
            self._ram_cache.delete(record_id)
        if self.index_manager is None:
            raise RuntimeError("Store is not set up. Call setup() first.")
        self.index_manager.delete(record_id)
        # Update query indexes
        if self.query_engine is not None:
            # Read old values for index removal — if not in cache, skip (index will be stale until rebuild)
            old = self._ram_cache.get(record_id) if self._ram_cache else None
            if old:
                self.query_engine.on_delete(record_id, old)
        
        self.invalidate_cache(record_id, metadata=None) # Signal deletion to workers

    def teardown(self) -> None:
        """Flush and close storage handles. Called during server shutdown."""
        from mkdb.server.event_log import emit as _emit
        _emit("info", f"store:{self.config.name}", "Store shutting down — flushing writes")
        if self._task_scheduler is not None:
            self._task_scheduler.stop()
        # Flush the write queue FIRST so all pending writes hit the log and
        # update the query engine's in-memory indexes before we save them.
        if self._write_queue is not None:
            self._write_queue.flush_all()
            self._write_queue.stop()
        if self.query_engine is not None:
            self.query_engine.save_all()
            print(f"[{self.config.name}] Query indexes saved.")
        if self.index_manager is not None and self.index_manager._dirty:
            self.index_manager.save_full()
        if self.log_manager is not None:
            self.log_manager.close()
        self.stop()  # stops query dispatcher
