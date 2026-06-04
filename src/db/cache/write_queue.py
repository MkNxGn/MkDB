"""
WriteQueue — debounced async write queue for one store.

Enqueued "write" operations are buffered and coalesced per record_id for
debounce_window seconds before being flushed to the registered handler.
Operations with op == "compact" or "rebuild_index" bypass debouncing and
are pushed directly to the queue.

A dead-letter list captures tasks whose handler raised an exception, capped
at 1000 entries (oldest dropped first).
"""

import logging
import queue
import threading
import time
from typing import Callable

logger = logging.getLogger(__name__)

WORKER_SENTINEL = object()


class WriteQueue:
    def __init__(self, debounce_window: float = 5.0, max_pending: int = 10_000):
        self.debounce_window = debounce_window
        self.max_pending     = max_pending

        self._queue:         queue.Queue = queue.Queue()
        self._pending:       dict        = {}    # record_id -> accumulated delta dict
        self._timers:        dict        = {}    # record_id -> enqueue timestamp
        self._handlers:      dict        = {}    # op_str -> Callable
        self._dead_letter:   list        = []    # failed tasks, capped at 1000
        self._stop_event:    threading.Event = threading.Event()
        self._worker_thread: threading.Thread = threading.Thread(
            target=self._worker_loop, daemon=True, name="WriteQueue-worker"
        )
        self._lock: threading.Lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background worker thread."""
        self._worker_thread.start()

    def register_handler(self, op: str, fn: Callable) -> None:
        """Map an operation string to a handler function."""
        self._handlers[op] = fn

    def enqueue(self, task: dict) -> None:
        """
        Add a task to the queue.

        For "write" ops: coalesce delta into _pending[record_id] and reset timer.
        For all other ops: push directly to _queue.

        Raises RuntimeError if _pending is at max_pending capacity.
        """
        op = task.get("op")
        if op == "write":
            record_id = task.get("record_id")
            delta     = task.get("delta", {})
            if record_id is None:
                logger.warning("WriteQueue: write task missing record_id, skipping")
                return
            with self._lock:
                if record_id not in self._pending and len(self._pending) >= self.max_pending:
                    raise RuntimeError(
                        f"WriteQueue pending buffer full ({self.max_pending} entries)"
                    )
                if record_id in self._pending:
                    self._pending[record_id].update(delta)
                else:
                    self._pending[record_id] = dict(delta)
                self._timers[record_id] = time.time()
        else:
            self._queue.put(task)

    def flush_all(self) -> None:
        """Force-drain all pending write timers immediately (call before stop)."""
        with self._lock:
            for record_id, delta in list(self._pending.items()):
                store = self._timers.get(record_id)   # reuse timer slot for store name
                # Build a flush task from whatever is in _pending
                task = {
                    "op":        "write",
                    "record_id": record_id,
                    "delta":     delta,
                    "ts":        time.time(),
                }
                self._queue.put(task)
            self._pending.clear()
            self._timers.clear()

    def stop(self) -> None:
        """Signal stop, flush, and join the worker thread (10 s timeout)."""
        self.flush_all()
        self._stop_event.set()
        self._queue.put(WORKER_SENTINEL)
        self._worker_thread.join(timeout=10)

    def get_dead_letters(self) -> list:
        """Return a copy of the dead-letter list for admin inspection."""
        return list(self._dead_letter)

    # ------------------------------------------------------------------
    # Worker loop (runs in daemon thread)
    # ------------------------------------------------------------------

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            # 1. Drain expired pending entries
            now = time.time()
            with self._lock:
                due = [
                    rid for rid, ts in self._timers.items()
                    if (now - ts) >= self.debounce_window
                ]
                for record_id in due:
                    task = {
                        "op":        "write",
                        "record_id": record_id,
                        "delta":     self._pending.pop(record_id),
                        "ts":        self._timers.pop(record_id),
                    }
                    self._queue.put(task)

            # 2. Pull and dispatch one item
            try:
                task = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue

            if task is WORKER_SENTINEL:
                break

            self._dispatch(task)

    def _dispatch(self, task: dict) -> None:
        op = task.get("op")
        handler = self._handlers.get(op)
        if handler is None:
            logger.warning("WriteQueue: no handler for op=%r", op)
            return
        try:
            handler(task)
        except Exception as exc:
            logger.error(
                "WriteQueue: handler for op=%r record_id=%r raised: %s",
                op, task.get("record_id"), exc,
            )
            self._dead_letter.append(task)
            if len(self._dead_letter) > 1000:
                self._dead_letter.pop(0)
