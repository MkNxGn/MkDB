"""
TaskScheduler — periodic daemon thread that checks segments and enqueues
compaction tasks via WriteQueue.
"""

import logging
import threading
import time

logger = logging.getLogger(__name__)


class TaskScheduler:
    def __init__(self, store, interval: float = None):
        """
        Parameters
        ----------
        store    : src.db.objects.store.store
        interval : check interval in seconds (default from file_config or 60)
        """
        self._store = store
        if interval is None:
            interval = float(getattr(store.config.file_config, "compaction_interval", 60))
        self.interval = interval
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name=f"TaskScheduler-{store.config.name}"
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def trigger_now(self, task_type: str = "compact") -> None:
        """Immediately enqueue a compaction task for all eligible segments."""
        self._run_checks(task_type)

    # ------------------------------------------------------------------
    # Internal loop
    # ------------------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop_event.wait(timeout=self.interval):
            self._run_checks("compact")

    def _run_checks(self, task_type: str) -> None:
        store = self._store
        if store.log_manager is None or store._write_queue is None:
            return
        from src.db.maintenance.compactor import Compactor
        compactor = Compactor(store)

        segments = store.log_manager.list_segments()
        # Skip the active segment
        active = store.log_manager.active_segment
        for seq_int in segments:
            if seq_int == active:
                continue
            seq_str = f"{seq_int:03d}"
            try:
                if task_type == "compact" and compactor.needs_compaction(seq_str):
                    logger.info("TaskScheduler: enqueuing compact for segment %s", seq_str)
                    store._write_queue.enqueue({
                        "op":        "compact",
                        "store":     store.config.name,
                        "record_id": None,
                        "delta":     {"segment": seq_int},
                        "ts":        time.time(),
                    })
            except Exception as exc:
                logger.warning("TaskScheduler: error checking segment %s: %s", seq_str, exc)
