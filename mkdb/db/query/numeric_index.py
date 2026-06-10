"""
NumericIndex — sorted numeric index for one numeric field of one store.

File format: idx_{service}_{field}_numeric.txt
Each line:   {value}:{id1},{id2}
File is kept in ascending sorted order by value.

RAM-threshold mode
------------------
If ram_threshold_bytes > 0 and the index file on disk exceeds that size,
the in-memory map is evicted after load/save.  All queries then read
directly from disk; pending mutations are buffered and flushed to disk
before each query and at each explicit save() call.
"""

import bisect
import logging
import os

logger = logging.getLogger(__name__)


class NumericIndex:
    def __init__(self, store_path: str, service: str, field: str,
                 ram_threshold_bytes: int = 0):
        safe_field = field.replace("/", "_").replace("\\", "_")
        self.path = os.path.join(store_path, f"idx_{service}_{safe_field}_numeric.txt")
        self._values: list = []              # sorted float values  (RAM mode)
        self._map:    dict = {}              # float -> set[rid]    (RAM mode)
        self._in_ram: bool = True
        self._ram_threshold: int = ram_threshold_bytes
        # Disk-mode pending buffers (only used when _in_ram is False)
        self._write_buf: dict = {}           # val -> set[rid] to add
        self._remove_buf: list = []          # list of (val, rid) to remove
        self.load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def load(self) -> None:
        if not os.path.exists(self.path):
            logger.debug("NumericIndex file %s does not exist.", self.path)
            return
        
        count = 0
        with open(self.path, "r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or ":" not in line:
                    continue
                val_str, _, ids_str = line.partition(":")
                try:
                    val = float(val_str)
                except ValueError:
                    continue
                ids = set(ids_str.split(",")) if ids_str else set()
                self._map[val] = ids
                count += len(ids)
        self._values = sorted(self._map.keys())
        logger.info("Loaded %d unique numeric values (%d record refs) from %s", len(self._map), count, self.path)
        self._apply_threshold()

    def save(self) -> None:
        if self._in_ram:
            self._write_file(self._map, self._values)
            self._apply_threshold()
        else:
            self._flush_pending()

    def _apply_threshold(self) -> None:
        """Evict map from RAM if the disk file exceeds the configured threshold."""
        if self._ram_threshold > 0 and os.path.exists(self.path):
            if os.path.getsize(self.path) > self._ram_threshold:
                self._map.clear()
                self._values.clear()
                self._in_ram = False

    def _write_file(self, data_map: dict, data_values: list) -> None:
        """Atomically write a map+sorted-values to disk."""
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            for val in data_values:
                ids = data_map.get(val)
                if ids:
                    fh.write(f"{val}:{','.join(sorted(ids))}\n")
        os.replace(tmp, self.path)

    def _load_disk_map(self) -> tuple:
        """Read the disk file and return (map, sorted_values) without touching self._map."""
        disk_map: dict = {}
        if os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8") as fh:
                for raw in fh:
                    line = raw.strip()
                    if not line or ":" not in line:
                        continue
                    val_str, _, ids_str = line.partition(":")
                    try:
                        val = float(val_str)
                    except ValueError:
                        continue
                    disk_map[val] = set(ids_str.split(",")) if ids_str else set()
        return disk_map, sorted(disk_map.keys())

    def _flush_pending(self) -> None:
        """Merge write/remove buffers into the disk file and clear them."""
        if not self._write_buf and not self._remove_buf:
            return
        disk_map, disk_values = self._load_disk_map()

        for val, ids in self._write_buf.items():
            if val not in disk_map:
                bisect.insort(disk_values, val)
                disk_map[val] = set()
            disk_map[val].update(ids)

        for val, rid in self._remove_buf:
            ids = disk_map.get(val)
            if ids:
                ids.discard(rid)
                if not ids:
                    del disk_map[val]
                    i = bisect.bisect_left(disk_values, val)
                    if i < len(disk_values) and disk_values[i] == val:
                        disk_values.pop(i)

        self._write_file(disk_map, disk_values)
        self._write_buf.clear()
        self._remove_buf.clear()

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add(self, record_id: str, numeric_value) -> None:
        val = float(numeric_value)
        if self._in_ram:
            if val not in self._map:
                bisect.insort(self._values, val)
                self._map[val] = set()
            self._map[val].add(record_id)
        else:
            self._write_buf.setdefault(val, set()).add(record_id)

    def remove(self, record_id: str, numeric_value) -> None:
        val = float(numeric_value)
        if self._in_ram:
            ids = self._map.get(val)
            if not ids:
                return
            ids.discard(record_id)
            if not ids:
                del self._map[val]
                i = bisect.bisect_left(self._values, val)
                if i < len(self._values) and self._values[i] == val:
                    self._values.pop(i)
        else:
            self._remove_buf.append((val, record_id))

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def exact_query(self, value) -> set:
        if self._in_ram:
            return set(self._map.get(float(value), set()))
        self._flush_pending()
        disk_map, _ = self._load_disk_map()
        return set(disk_map.get(float(value), set()))

    def range_query(self, lo=None, hi=None,
                    lo_inclusive: bool = True, hi_inclusive: bool = True) -> set:
        """
        Return all record IDs whose value falls in [lo, hi] (bounds optional).
        lo/hi may be None for open-ended ranges.
        """
        if self._in_ram:
            return self._range_from(self._map, self._values, lo, hi, lo_inclusive, hi_inclusive)
        self._flush_pending()
        disk_map, disk_values = self._load_disk_map()
        return self._range_from(disk_map, disk_values, lo, hi, lo_inclusive, hi_inclusive)

    @staticmethod
    def _range_from(data_map: dict, data_values: list,
                    lo, hi, lo_inclusive: bool, hi_inclusive: bool) -> set:
        if lo is None:
            lo_idx = 0
        else:
            lo_f = float(lo)
            lo_idx = (bisect.bisect_left(data_values, lo_f) if lo_inclusive
                      else bisect.bisect_right(data_values, lo_f))

        if hi is None:
            hi_idx = len(data_values)
        else:
            hi_f = float(hi)
            hi_idx = (bisect.bisect_right(data_values, hi_f) if hi_inclusive
                      else bisect.bisect_left(data_values, hi_f))

        result: set = set()
        for val in data_values[lo_idx:hi_idx]:
            result |= data_map.get(val, set())
        return result
