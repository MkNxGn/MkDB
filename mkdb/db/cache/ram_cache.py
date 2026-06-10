"""
RamCache — in-process record cache for one store.

Supports three eviction strategies controlled by ram_config.clean_type:
  "lru" — evict least-recently-used entry
  "lfu" — evict least-frequently-used entry
  "ttl" — evict only entries whose TTL has expired (no count-based eviction)

All public methods are thread-safe via a single threading.Lock.
"""

import threading
import time
from collections import OrderedDict


class RamCache:
    def __init__(self, max_size: int, ttl: int, clean_type: str = "lru"):
        self.max_size   = max_size
        self.ttl        = ttl           # seconds; 0 = never expire
        self.clean_type = clean_type

        self._store:       dict          = {}             # record_id -> flat dict
        self._access_log:  OrderedDict   = OrderedDict()  # record_id -> None (LRU order)
        self._freq:        dict          = {}             # record_id -> access count (LFU)
        self._timestamps:  dict          = {}             # record_id -> last_access float
        self._lock:        threading.Lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, record_id: str):
        """Return the cached flat dict, or None on miss / expired entry."""
        with self._lock:
            if record_id not in self._store:
                return None
            if self._is_expired(record_id):
                self._evict_one(record_id)
                return None
            self._touch(record_id)
            return dict(self._store[record_id])   # return a shallow copy

    def set(self, record_id: str, flat_dict: dict) -> None:
        """Insert or overwrite an entry, evicting if over max_size."""
        with self._lock:
            if record_id in self._store:
                self._store[record_id] = dict(flat_dict)
                self._touch(record_id)
            else:
                self._store[record_id] = dict(flat_dict)
                self._access_log[record_id] = None
                self._freq[record_id] = 1
                self._timestamps[record_id] = time.time()
                while len(self._store) > self.max_size:
                    self.evict()

    def delete(self, record_id: str) -> None:
        """Remove an entry from the cache."""
        with self._lock:
            self._evict_one(record_id)

    def size(self) -> int:
        """Return the current number of cached entries."""
        with self._lock:
            return len(self._store)

    def estimated_bytes(self) -> int:
        """
        Rough estimate of memory used by cached data.
        Counts the total character length of all cached flat-dict values.
        Accurate enough for dashboard display; not a true malloc measurement.
        """
        import sys
        with self._lock:
            total = 0
            for record in self._store.values():
                for k, v in record.items():
                    total += sys.getsizeof(k) + sys.getsizeof(v)
            total += sys.getsizeof(self._store)
            return total

    def apply_delta(self, record_id: str, delta: dict, nested_enabled: bool = False) -> None:
        """
        Merge delta paths into the existing cached object.
        Supports both shallow keys and dot-notation paths (e.g. "a.b") via partitioning 
        if nested_enabled is True.
        """
        from mkdb.objects import partition_object, deep_update
        with self._lock:
            if nested_enabled:
                partitioned = partition_object(delta)
                if record_id in self._store:
                    self._store[record_id] = deep_update(self._store[record_id], partitioned)
                    self._touch(record_id)
                else:
                    self._store[record_id] = partitioned
                    self._access_log[record_id] = None
                    self._freq[record_id] = 1
                    self._timestamps[record_id] = time.time()
            else:
                if record_id in self._store:
                    self._store[record_id].update(delta)
                    self._touch(record_id)
                else:
                    self._store[record_id] = dict(delta)
                    self._access_log[record_id] = None
                    self._freq[record_id] = 1
                    self._timestamps[record_id] = time.time()

    def evict(self) -> None:
        """
        Evict one entry according to the configured strategy.
        Called with _lock already held.
        """
        if not self._store:
            return
        if self.clean_type == "lru":
            oldest_id = next(iter(self._access_log))
            self._evict_one(oldest_id)
        elif self.clean_type == "lfu":
            lfu_id = min(self._freq, key=lambda k: self._freq[k])
            self._evict_one(lfu_id)
        elif self.clean_type == "ttl":
            # Evict all expired entries
            now = time.time()
            expired = [rid for rid, ts in self._timestamps.items()
                       if self.ttl > 0 and (now - ts) > self.ttl]
            for rid in expired:
                self._evict_one(rid)
            # If nothing expired but we're still over max_size, fall back to LRU
            if len(self._store) > self.max_size:
                oldest_id = next(iter(self._access_log))
                self._evict_one(oldest_id)

    # ------------------------------------------------------------------
    # Internal helpers (all called with _lock held)
    # ------------------------------------------------------------------

    def _touch(self, record_id: str) -> None:
        """Update LRU order, frequency, and timestamp for an accessed entry."""
        self._access_log.move_to_end(record_id)
        self._freq[record_id] = self._freq.get(record_id, 0) + 1
        self._timestamps[record_id] = time.time()

    def _is_expired(self, record_id: str) -> bool:
        if self.ttl <= 0:
            return False
        ts = self._timestamps.get(record_id, 0)
        return (time.time() - ts) > self.ttl

    def _evict_one(self, record_id: str) -> None:
        """Remove a single record_id from all internal structures."""
        self._store.pop(record_id, None)
        self._access_log.pop(record_id, None)
        self._freq.pop(record_id, None)
        self._timestamps.pop(record_id, None)
