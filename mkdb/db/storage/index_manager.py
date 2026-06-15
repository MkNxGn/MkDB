"""
IndexManager — manages the primary index for one store.

Index file: {store_path}/{service}.idx  (or .txt)
Line format: {record_id}:{segment_seq}:{byte_offset}:{byte_size}
Tombstone:   !{record_id}:{segment_seq}:{byte_offset}:{byte_size}

Tombstoned entries are excluded from _map at load time; the physical file
is only fully rewritten during compaction via save_full().
"""

import logging
import os

logger = logging.getLogger(__name__)


class IndexManager:
    def __init__(self, store_path: str, service: str):
        self.store_path = store_path
        self.service = service
        # We prefer .idx but allow fallback to .txt if that's what's on disk.
        idx_path = os.path.join(store_path, f"{service}.idx")
        txt_path = os.path.join(store_path, f"{service}.txt")

        if os.path.exists(idx_path):
            self.index_path = idx_path
        elif os.path.exists(txt_path):
            self.index_path = txt_path
        else:
            # For new stores, we'll default to .idx unless the user's feedback
            # suggests they want everything to be .txt now.
            self.index_path = idx_path

        self._map: dict = {}   # record_id -> (segment_seq_str, offset, size)
        self._dirty: bool = False
        self.load()

    # ------------------------------------------------------------------
    # Load / save
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Parse the .idx file into _map, skipping tombstoned entries."""
        if not os.path.exists(self.index_path):
            logger.info(f"Index file {self.index_path} does not exist. Checking for log files to bootstrap...")
            self._bootstrap_from_logs()
            return
        
        count = 0
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
                count += 1
        logger.info(f"Loaded {count} primary index entries from {self.index_path} ({len(self._map)} live)")

    def _bootstrap_from_logs(self) -> None:
        """Scan {store_path} for {service}_NNN.log files and rebuild _map."""
        import re
        pattern = re.compile(rf"^{re.escape(self.service)}_(\d{{3}})\.log$")
        log_files = []
        
        for name in os.listdir(self.store_path):
            m = pattern.match(name)
            if m:
                log_files.append((int(m.group(1)), name))
        
        if not log_files:
            logger.info(f"No log files found in {self.store_path}. Starting empty.")
            return

        log_files.sort() # Process in order 001, 002...
        logger.info(f"Found {len(log_files)} log files. Bootstrapping index...")
        
        from mkdb.db.storage.serializer import deserialize_record
        count = 0
        for seq_int, filename in log_files:
            seq_str = f"{seq_int:03d}"
            path = os.path.join(self.store_path, filename)
            offset = 0
            with open(path, "rb") as fh:
                # Iterate by lines as bytes to track byte offsets correctly
                for line_bytes in fh:
                    size = len(line_bytes)
                    line_str = line_bytes.decode("utf-8", errors="replace")
                    record_id, _ = deserialize_record(line_str)
                    if record_id:
                        self._map[record_id] = (seq_str, offset, size)
                        count += 1
                    else:
                        # Might be a tombstone written to log? WS-1 doesn't specify log tombstones yet,
                        # usually tombstones are only in the .idx and compaction removes them.
                        pass
                    offset += size
        
        logger.info(f"Bootstrapped {count} records from logs into index ({len(self._map)} live).")
        self.save_full() # Save the newly created index

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

    def all_record_ids(self) -> list[str]:
        """Return a list of all current live record IDs."""
        return list(self._map.keys())

    def records_in_segment(self, seq_str: str) -> list:
        """Return all record IDs whose current segment matches seq_str."""
        return [rid for rid, (seg, _, _) in self._map.items() if seg == seq_str]

