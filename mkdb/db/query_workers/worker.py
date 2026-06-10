"""
QueryWorker — runs inside a worker process (or thread) to resolve QueryTasks.

Each worker owns:
  - A lightweight LRU + TTL RAM cache keyed by record_id.
  - Read-only access to the store's disk files via the storage layer.
  - A private invalidation queue so the dispatcher can evict stale entries.

Entry points
------------
worker_process_main  — for multiprocessing.Process targets
worker_thread_main   — for threading.Thread targets (single-worker / no-GIL mode)

Both share the same _worker_loop implementation; the only difference is how
sys.path is initialised (process needs it, thread already has it).
"""

import os
import sys
import time
import queue
import logging
from collections import OrderedDict
from typing import Any, Optional

# The sentinel object placed in the work queue to signal graceful shutdown.
WORKER_SENTINEL = None

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Minimal worker-local LRU + TTL cache
# (A full RamCache from WS-2 will replace this once implemented.)
# ---------------------------------------------------------------------------

class _WorkerCache:
    """
    Lightweight LRU cache with per-entry TTL.

    Operations are O(1) (dict + OrderedDict).
    """

    def __init__(self, max_size: int, ttl: float):
        self._max  = max(1, max_size)
        self._ttl  = float(ttl)
        self._data: dict[str, Any]          = {}
        self._order: OrderedDict[str, None] = OrderedDict()
        self._ts:   dict[str, float]        = {}

    # -- public API ----------------------------------------------------------

    def get(self, record_id: str) -> Optional[Any]:
        if record_id not in self._data:
            return None
        if time.monotonic() - self._ts[record_id] > self._ttl:
            self._evict_one(record_id)
            return None
        self._order.move_to_end(record_id)
        return self._data[record_id]

    def set(self, record_id: str, value: Any) -> None:
        if record_id in self._data:
            self._order.move_to_end(record_id)
        else:
            if len(self._data) >= self._max:
                oldest, _ = self._order.popitem(last=False)
                del self._data[oldest]
                del self._ts[oldest]
            self._order[record_id] = None
        self._data[record_id] = value
        self._ts[record_id]   = time.monotonic()

    def delete(self, record_id: str) -> None:
        self._evict_one(record_id)

    def clear(self) -> None:
        self._data.clear()
        self._order.clear()
        self._ts.clear()

    def __len__(self) -> int:
        return len(self._data)

    # -- internal ------------------------------------------------------------

    def _evict_one(self, record_id: str) -> None:
        self._data.pop(record_id, None)
        self._order.pop(record_id, None)
        self._ts.pop(record_id, None)


# ---------------------------------------------------------------------------
# Task resolution — integration point for WS-1 and WS-3
# ---------------------------------------------------------------------------

def _resolve(task_dict: dict, cache: _WorkerCache, base_path: str) -> Any:
    """
    Dispatch a task dict to the correct resolver.

    Integration notes
    -----------------
    WS-1 (storage layer):
        Replace the NotImplementedError blocks in _read and _exists with:
            index_mgr = IndexManager(base_path, store_name)
            log_mgr   = LogManager(base_path, store_name)
            raw_line  = log_mgr.read(*index_mgr.get(record_id))
            # parse flat line -> dict

    WS-3 (query engine):
        Replace the NotImplementedError blocks in _query and _count with:
            engine = QueryEngine(store)
            return engine.query(filter_dict)
    """
    op         = task_dict["operation"]
    store_name = task_dict["store_name"]
    params     = task_dict["params"]

    if op == "read":
        return _read(store_name, params, cache, base_path)
    if op == "multi_read":
        return _multi_read(store_name, params, cache, base_path)
    if op == "exists":
        return _exists(store_name, params, cache, base_path)
    if op == "query":
        return _query(store_name, params, cache, base_path)
    if op == "count":
        return _count(store_name, params, cache, base_path)
    raise ValueError(f"Unknown operation: {op!r}")


def _read(store_name: str, params: dict, cache: _WorkerCache, base_path: str) -> dict:
    record_id = params.get("record_id", "")
    if not record_id:
        raise ValueError("record_id required for 'read'")

    cached = cache.get(record_id)
    if cached is not None:
        return cached

    # Basic bootstrap for worker storage access
    from mkdb.db.storage.index_manager import IndexManager
    from mkdb.db.storage.log_manager import LogManager
    from mkdb.db.storage.serializer import deserialize_record

    # Lazy-init or use cached managers to avoid reloading index on every seek
    # Note: In a production environment, we'd use a more formal Context object.
    if not hasattr(_read, "_managers"):
        # We don't have the full config here, so we use a large dummy threshold 
        # for LogManager since we are only reading (never writing from workers).
        idx = IndexManager(base_path, store_name)
        lmgr = LogManager(base_path, store_name, 10 ** 9) 
        _read._managers = (idx, lmgr)
    else:
        idx, lmgr = _read._managers

    entry = idx.get(record_id)
    if entry is None:
        return None
    
    seg, offset, size = entry
    raw = lmgr.read(seg, offset, size)
    _, flat_dict = deserialize_record(raw)
    
    result = {"_id": record_id, **flat_dict}
    cache.set(record_id, result)
    return result


def _multi_read(store_name: str, params: dict, cache: _WorkerCache, base_path: str) -> dict:
    record_ids = params.get("record_ids", [])
    results = {}
    for rid in record_ids:
        res = _read(store_name, {"record_id": rid}, cache, base_path)
        if res:
            results[rid] = res
    return results


def _exists(store_name: str, params: dict, cache: _WorkerCache, base_path: str) -> bool:
    record_id = params.get("record_id", "")
    if not record_id:
        raise ValueError("record_id required for 'exists'")

    if cache.get(record_id) is not None:
        return True

    from mkdb.db.storage.index_manager import IndexManager
    if not hasattr(_read, "_managers"):
        idx = IndexManager(base_path, store_name)
    else:
        idx = _read._managers[0]
        
    return idx.get(record_id) is not None


def _query(store_name: str, params: dict, cache: _WorkerCache, base_path: str) -> dict:
    """Execute query and return result dict {ids, count, total_matches}."""
    # We need a QueryEngine to resolve this. 
    # QueryEngine requires a Store-like object.
    from mkdb.db.query.query_engine import QueryEngine
    
    if not hasattr(_query, "_engine"):
        # Create a lightweight proxy for the store
        class WorkerStoreProxy:
            def __init__(self, name, path, config_dict):
                from mkdb.db.storage.index_manager import IndexManager
                from mkdb.db.storage.log_manager import LogManager
                from mkdb.config.db import store_config
                self.config = store_config(config_dict)
                self.store_path = path 
                self.index_manager = IndexManager(path, name)
                self.log_manager = LogManager(path, name, 10**9)
            def read(self, rid):
                return _read(store_name, {"record_id": rid}, cache, base_path)
        
        config_dict = getattr(_worker_loop, "store_config", {})
        proxy = WorkerStoreProxy(store_name, base_path, config_dict)
        _query._engine = QueryEngine(proxy)
        _query._engine.build_indexes()
        
    return _query._engine.query(params)


def _count(store_name: str, params: dict, cache: _WorkerCache, base_path: str) -> int:
    res = _query(store_name, params, cache, base_path)
    return res.get("total_matches", 0)


# ---------------------------------------------------------------------------
# Core worker loop — shared by process and thread entry points
# ---------------------------------------------------------------------------

def _worker_loop(
    worker_id:          int,
    store_name:         str,
    base_path:          str,
    work_queue,         # multiprocessing.Queue
    results_queue,      # multiprocessing.Queue
    invalidation_queue, # multiprocessing.Queue (private to this worker)
    cache_max_size:     int,
    cache_ttl:          float,
    stop_event,         # multiprocessing.Event or threading.Event
    store_config_dict:  dict = {},
) -> None:
    log = logging.getLogger(f"QueryWorker[{store_name}#{worker_id}]")
    log.info("Worker started (pid=%s)", os.getpid())

    cache = _WorkerCache(cache_max_size, cache_ttl)

    # Store config for use by _resolve (e.g. QueryEngine bridge)
    _worker_loop.store_config = store_config_dict

    while not stop_event.is_set():
        # 1. Drain the private invalidation queue to keep cache consistent
        #    with writes that the dispatcher has broadcast.
        try:
            while True:
                msg = invalidation_queue.get_nowait()
                rid = None
                meta = None
                
                if isinstance(msg, dict):
                    rid = msg.get("id")
                    meta = msg.get("meta")
                else:
                    rid = msg # Legacy support

                if rid:
                    cache.delete(rid)
                    # Update local IndexManager views if they exist to prevent drift
                    # from the main process's disk state.
                    if hasattr(_read, "_managers"):
                        idx = _read._managers[0]
                        if meta:
                            idx._map[rid] = meta
                        else:
                            idx._map.pop(rid, None)
        except Exception:
            pass  # queue.Empty or similar — expected

        # 2. Pull the next task (short timeout so stop_event is checked)
        try:
            task_dict = work_queue.get(timeout=0.5)
        except Exception:
            continue  # timeout — loop back to check stop_event

        # Sentinel signals graceful shutdown
        if task_dict is WORKER_SENTINEL:
            log.info("Received shutdown sentinel")
            break

        task_id = task_dict.get("task_id", "?")
        log.debug("Handling task %s op=%s", task_id, task_dict.get("operation"))

        # 3. Resolve and post result
        try:
            data = _resolve(task_dict, cache, base_path)
            results_queue.put({
                "task_id": task_id,
                "status":  "ok",
                "data":    data,
            })
        except Exception as exc:
            log.warning("Task %s failed: %s", task_id, exc)
            results_queue.put({
                "task_id": task_id,
                "status":  "error",
                "error":   str(exc),
            })

    log.info("Worker exiting (pid=%s)", os.getpid())


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def worker_process_main(
    worker_id:          int,
    project_root:       str,       # added to sys.path so imports resolve
    store_name:         str,
    base_path:          str,
    work_queue,
    results_queue,
    invalidation_queue,
    cache_max_size:     int,
    cache_ttl:          float,
    stop_event,
    store_config_dict:  dict = {},
) -> None:
    """Entry point for multiprocessing.Process workers."""
    # Ensure the project is importable inside the child process
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    logging.basicConfig(level=logging.INFO)
    _worker_loop(
        worker_id, store_name, base_path,
        work_queue, results_queue, invalidation_queue,
        cache_max_size, cache_ttl, stop_event,
        store_config_dict
    )


def worker_thread_main(
    worker_id:          int,
    store_name:         str,
    base_path:          str,
    work_queue,
    results_queue,
    invalidation_queue,
    cache_max_size:     int,
    cache_ttl:          float,
    stop_event,
    store_config_dict:  dict = {},
) -> None:
    """Entry point for threading.Thread workers (parallel_enabled=False)."""
    _worker_loop(
        worker_id, store_name, base_path,
        work_queue, results_queue, invalidation_queue,
        cache_max_size, cache_ttl, stop_event,
        store_config_dict
    )
