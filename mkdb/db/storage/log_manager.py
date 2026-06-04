"""
LogManager — append-only rolling log file manager for one store.

Segment files: {store_path}/{service}_{NNN}.log   (NNN = zero-padded 3-digit int)
On init, scans store_path for existing segments and opens the highest one
in append mode.  When a segment exceeds segment_threshold bytes it is closed
and a new one is started (_rollover).
"""

import os
import re


class LogManager:
    def __init__(self, store_path: str, service: str, segment_threshold: int):
        """
        Parameters
        ----------
        store_path         : absolute path to the store directory
        service            : store name (used as the filename prefix)
        segment_threshold  : byte size at which a new segment is opened
        """
        self.store_path = store_path
        self.service = service
        self.segment_threshold = segment_threshold

        self.active_segment: int = self._find_highest_segment()
        seg_path = self.segment_path(self.active_segment)
        self.active_fh = open(seg_path, "ab")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_highest_segment(self) -> int:
        """Return the highest existing segment number, or 1 if none exist."""
        pattern = re.compile(rf"^{re.escape(self.service)}_(\d{{3}})\.log$")
        highest = 0
        for name in os.listdir(self.store_path):
            m = pattern.match(name)
            if m:
                n = int(m.group(1))
                if n > highest:
                    highest = n
        if highest == 0:
            highest = 1
            open(self.segment_path(1), "ab").close()
        return highest

    def segment_path(self, seq_int: int) -> str:
        """Return the absolute path for segment number seq_int."""
        return os.path.join(self.store_path, f"{self.service}_{seq_int:03d}.log")

    def _rollover(self) -> None:
        """Close the current segment and open a new one."""
        self.active_fh.flush()
        self.active_fh.close()
        self.active_segment += 1
        self.active_fh = open(self.segment_path(self.active_segment), "ab")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def append(self, record_id: str, flat_line_str: str) -> tuple:
        """
        Write one record line to the active segment.

        Parameters
        ----------
        record_id     : the record's ID (used only for return value metadata)
        flat_line_str : already-serialized line string (no trailing newline)

        Returns
        -------
        (segment_seq_str, byte_offset, byte_size)
        """
        encoded = (flat_line_str + "\n").encode("utf-8")
        offset = self.active_fh.tell()
        self.active_fh.write(encoded)
        self.active_fh.flush()
        size = len(encoded)
        seg_str = f"{self.active_segment:03d}"
        if self.active_fh.tell() >= self.segment_threshold:
            self._rollover()
        return (seg_str, offset, size)

    def read(self, segment_seq_str: str, offset: int, size: int) -> str:
        """
        Read exactly `size` bytes from the indicated segment at `offset`.
        Uses stored size — does NOT use readline to avoid partial reads.
        Returns the decoded string with trailing newline stripped.
        """
        # Handle blob entries (segment_seq_str starts with "blobs/")
        if segment_seq_str.startswith("blobs/"):
            blob_path = os.path.join(self.store_path, segment_seq_str)
            with open(blob_path, "r", encoding="utf-8") as fh:
                return fh.read()
        seq_int = int(segment_seq_str)
        path = self.segment_path(seq_int)
        with open(path, "rb") as fh:
            fh.seek(offset)
            return fh.read(size).decode("utf-8").rstrip("\n")

    def list_segments(self) -> list:
        """Return all segment numbers found on disk, sorted ascending."""
        pattern = re.compile(rf"^{re.escape(self.service)}_(\d{{3}})\.log$")
        nums = []
        for name in os.listdir(self.store_path):
            m = pattern.match(name)
            if m:
                nums.append(int(m.group(1)))
        return sorted(nums)

    def close(self) -> None:
        """Flush and close the active file handle."""
        if self.active_fh and not self.active_fh.closed:
            self.active_fh.flush()
            self.active_fh.close()
