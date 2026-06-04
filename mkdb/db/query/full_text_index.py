"""
FullTextIndex — inverted index for one text field of one store.

File format: idx_{service}_{field}_words.txt
Each line:   {stem}:{id1},{id2},{id3}

RAM-threshold mode
------------------
If ram_threshold_bytes > 0 and the index file on disk exceeds that size,
the in-memory map is evicted.  Pending mutations are buffered and flushed
to disk before each query and at each explicit save() call.
"""

import os
from mkdb.db.query.tokenizer import tokenize


class FullTextIndex:
    def __init__(self, store_path: str, service: str, field: str,
                 ram_threshold_bytes: int = 0):
        safe_field = field.replace("/", "_").replace("\\", "_")
        self.path = os.path.join(store_path, f"idx_{service}_{safe_field}_words.txt")
        self._map: dict = {}          # stem -> set[rid]  (RAM mode)
        self._in_ram: bool = True
        self._ram_threshold: int = ram_threshold_bytes
        # Disk-mode pending buffers (only used when _in_ram is False)
        self._add_buf: dict = {}      # stem -> set[rid] to add
        self._remove_buf: dict = {}   # stem -> set[rid] to remove
        self.load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Load the index from disk into _map."""
        if not os.path.exists(self.path):
            return
        with open(self.path, "r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or ":" not in line:
                    continue
                stem, _, ids_str = line.partition(":")
                ids = set(ids_str.split(",")) if ids_str else set()
                self._map[stem] = ids
        self._apply_threshold()

    def save(self) -> None:
        """Write the index to disk."""
        if self._in_ram:
            self._write_file(self._map)
            self._apply_threshold()
        else:
            self._flush_pending()

    def _apply_threshold(self) -> None:
        """Evict map from RAM if the disk file exceeds the configured threshold."""
        if self._ram_threshold > 0 and os.path.exists(self.path):
            if os.path.getsize(self.path) > self._ram_threshold:
                self._map.clear()
                self._in_ram = False

    def _write_file(self, data_map: dict) -> None:
        """Atomically write a stem->ids map to disk."""
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            for stem, ids in data_map.items():
                if ids:
                    fh.write(f"{stem}:{','.join(sorted(ids))}\n")
        os.replace(tmp, self.path)

    def _load_disk_map(self) -> dict:
        """Read the disk file and return a stem->set map without touching self._map."""
        disk_map: dict = {}
        if os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8") as fh:
                for raw in fh:
                    line = raw.strip()
                    if not line or ":" not in line:
                        continue
                    stem, _, ids_str = line.partition(":")
                    disk_map[stem] = set(ids_str.split(",")) if ids_str else set()
        return disk_map

    def _flush_pending(self) -> None:
        """Merge add/remove buffers into the disk file and clear them."""
        if not self._add_buf and not self._remove_buf:
            return
        disk_map = self._load_disk_map()

        for stem, ids in self._add_buf.items():
            disk_map.setdefault(stem, set()).update(ids)

        for stem, ids in self._remove_buf.items():
            existing = disk_map.get(stem)
            if existing:
                existing -= ids
                if not existing:
                    del disk_map[stem]

        self._write_file(disk_map)
        self._add_buf.clear()
        self._remove_buf.clear()

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add(self, record_id: str, text_value: str) -> None:
        """Tokenize text_value and add record_id to every stem's set."""
        if self._in_ram:
            for stem in tokenize(text_value):
                self._map.setdefault(stem, set()).add(record_id)
        else:
            for stem in tokenize(text_value):
                self._add_buf.setdefault(stem, set()).add(record_id)

    def remove(self, record_id: str, text_value: str) -> None:
        """Remove record_id from every stem's set; prune empty stems."""
        if self._in_ram:
            for stem in tokenize(text_value):
                ids = self._map.get(stem)
                if ids:
                    ids.discard(record_id)
                    if not ids:
                        del self._map[stem]
        else:
            for stem in tokenize(text_value):
                self._remove_buf.setdefault(stem, set()).add(record_id)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def search(self, keywords: list, mode: str = "and") -> set:
        """
        Search for records matching the given keywords.

        keywords : list of raw keyword strings (will be tokenised)
        mode     : "and" (intersection) | "or" (union)
        Returns a set of record IDs.
        """
        if not keywords:
            return set()
        if self._in_ram:
            data_map = self._map
        else:
            self._flush_pending()
            data_map = self._load_disk_map()
        candidate_sets = []
        for kw in keywords:
            ids = set()
            for stem in tokenize(kw):
                ids |= data_map.get(stem, set())
            candidate_sets.append(ids)
        if not candidate_sets:
            return set()
        if mode == "or":
            result = set()
            for s in candidate_sets:
                result |= s
            return result
        # AND: intersect across keywords
        result = candidate_sets[0]
        for s in candidate_sets[1:]:
            result = result & s
        return result
