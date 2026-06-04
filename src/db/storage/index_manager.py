"""
IndexManager — manages the primary index for one store.

Index file: {store_path}/{service}.idx
Line format: {record_id}:{segment_seq}:{byte_offset}:{byte_size}
Tombstone:   !{record_id}:{segment_seq}:{byte_offset}:{byte_size}

Tombstoned entries are excluded from _map at load time; the physical .idx
file is only fully rewritten during compaction via save_full().
"""

import os


class IndexManager:
    def __init__(self, store_path: str, service: str):
        self.index_path = os.path.join(store_path, f"{service}.idx")
        self._map: dict = {}   # record_id -> (segment_seq_str, offset, size)
        self._dirty: bool = False
        self.load()

    # ------------------------------------------------------------------
    # Load / save
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Parse the .idx file into _map, skipping tombstoned entries."""
        if not os.path.exists(self.index_path):
            return
        with open(self.index_path, "r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                if line.startswith("!"):
                    # Tombstone — ensure it's absent from the map
                    record_id = line[1:].split(":")[0]
                    self._map.pop(record_id, None)
                    continue
                parts = line.split(":")
                if len(parts) < 4:
                    continue
                # segment_seq may be a path like "blobs/abc.dat" containing "/"
                # Format: record_id:segment_seq:offset:size
                # We split on the last two colons to get offset and size safely
                record_id = parts[0]
                size = int(parts[-1])
                offset = int(parts[-2])
                segment_seq = ":".join(parts[1:-2])
                self._map[record_id] = (segment_seq, offset, size)

    def save_full(self) -> None:
        """Rewrite the entire .idx file from _map (used after compaction)."""
        tmp_path = self.index_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as fh:
            for record_id, (seg, offset, size) in self._map.items():
                fh.write(f"{record_id}:{seg}:{offset}:{size}\n")
        os.replace(tmp_path, self.index_path)
        self._dirty = False

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def get(self, record_id: str):
        """Return (segment_seq, offset, size) or None."""
        return self._map.get(record_id)

    def set(self, record_id: str, segment_seq: str, offset: int, size: int) -> None:
        """Update _map and append the new entry to the .idx file."""
        self._map[record_id] = (segment_seq, offset, size)
        with open(self.index_path, "a", encoding="utf-8") as fh:
            fh.write(f"{record_id}:{segment_seq}:{offset}:{size}\n")
        self._dirty = True

    def delete(self, record_id: str) -> None:
        """Remove from _map and write a tombstone to the .idx file."""
        if record_id not in self._map:
            return
        entry = self._map.pop(record_id)
        seg, offset, size = entry
        with open(self.index_path, "a", encoding="utf-8") as fh:
            fh.write(f"!{record_id}:{seg}:{offset}:{size}\n")
        self._dirty = True

    def records_in_segment(self, seq_str: str) -> list:
        """Return all record IDs whose current segment matches seq_str."""
        return [rid for rid, (seg, _, _) in self._map.items() if seg == seq_str]

    def all_record_ids(self) -> list:
        """Return all live record IDs."""
        return list(self._map.keys())
