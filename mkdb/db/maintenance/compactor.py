"""
Compactor — rewrites log segments to reclaim space from deleted/overwritten records.

For each segment:
1. Collect all active record IDs whose current segment == this segment.
2. Re-read each record line and write it to a .tmp file, tracking new offsets.
3. Atomically replace the .log with the .tmp via os.replace.
4. Batch-update IndexManager with new offsets and call save_full().
"""

import logging
import os

logger = logging.getLogger(__name__)


class Compactor:
    def __init__(self, store):
        """
        Parameters
        ----------
        store : src.db.objects.store.store
        """
        self._store = store

    # ------------------------------------------------------------------
    # Size helpers
    # ------------------------------------------------------------------

    def compute_logical_size(self, seq_str: str) -> int:
        """Sum of all stored entry sizes in index_manager._map for this segment."""
        idx = self._store.index_manager
        if idx is None:
            return 0
        return sum(
            size
            for _rid, (seg, _off, size) in idx._map.items()
            if seg == seq_str
        )

    def needs_compaction(self, seq_str: str, dead_ratio_threshold: float = None) -> bool:
        """
        Return True if the dead-record ratio exceeds the threshold.
        dead_ratio = (physical_size - logical_size) / physical_size
        """
        store = self._store
        if store.log_manager is None:
            return False
        # Default threshold from config
        if dead_ratio_threshold is None:
            dead_ratio_threshold = getattr(
                store.config.file_config, "compaction_dead_ratio", 0.3
            )
        seq_int = int(seq_str)
        path = store.log_manager.segment_path(seq_int)
        if not os.path.exists(path):
            return False
        physical_size = os.path.getsize(path)
        if physical_size == 0:
            return False
        logical_size = self.compute_logical_size(seq_str)
        ratio = (physical_size - logical_size) / physical_size
        return ratio > dead_ratio_threshold

    # ------------------------------------------------------------------
    # Compact
    # ------------------------------------------------------------------

    def compact_segment(self, seq_int: int) -> None:
        """
        Compact one log segment in place.
        Skips the active (currently-written) segment to avoid corruption.
        """
        store = self._store
        if store.log_manager is None or store.index_manager is None:
            raise RuntimeError("Store storage not initialised.")

        # Never compact the active segment (it has an open file handle)
        if seq_int == store.log_manager.active_segment:
            logger.info("compact_segment: skipping active segment %03d", seq_int)
            return

        seq_str = f"{seq_int:03d}"
        active_ids = store.index_manager.records_in_segment(seq_str)
        if not active_ids:
            logger.info("compact_segment: segment %s has no live records, skipping", seq_str)
            return

        seg_path = store.log_manager.segment_path(seq_int)
        tmp_path = seg_path + ".tmp"

        new_offsets: dict = {}   # record_id -> (seq_str, new_offset, new_size)

        with open(tmp_path, "w", encoding="utf-8") as out_fh:
            for record_id in active_ids:
                entry = store.index_manager.get(record_id)
                if entry is None:
                    continue
                seg, offset, size = entry
                try:
                    line_str = store.log_manager.read(seg, offset, size)
                except Exception as exc:
                    logger.warning("compact_segment: could not read %s: %s", record_id, exc)
                    continue
                # Write to .tmp
                new_offset = out_fh.tell()
                out_fh.write(line_str + "\n")
                new_size = len((line_str + "\n").encode("utf-8"))
                new_offsets[record_id] = (seq_str, new_offset, new_size)

        # Atomic swap
        os.replace(tmp_path, seg_path)
        logger.info("compact_segment: segment %s compacted (%d records kept)", seq_str, len(new_offsets))

        # Update index
        for record_id, (seg, new_offset, new_size) in new_offsets.items():
            store.index_manager._map[record_id] = (seg, new_offset, new_size)
        store.index_manager.save_full()
