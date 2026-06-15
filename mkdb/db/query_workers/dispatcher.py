"""
QueryDispatcher — the main-process facade for the query worker pool.

Responsibilities
----------------
1.  Start / stop worker processes or threads based on config.
2.  Accept submit() calls from any thread, assign a task_id, put the task
    on the shared work queue, and block until the worker posts a result.
3.  Route results from the shared results queue back to the correct waiter
    via a lightweight result-router daemon thread.
4.  Broadcast cache-invalidation messages to every worker's private queue
    so their caches stay consistent with writes.

Parallel vs. single-worker mode
--------------------------------
parallel_enabled=False  →  one threading.Thread worker in the same process.
                            Shares the GIL; suitable for I/O-bound reads.

parallel_enabled=True   →  N multiprocessing.Process workers.
                            Each has its own GIL and Python heap — suitable
                            for CPU-bound query scans (WS-3).
                            N defaults to os.cpu_count() when worker_count=0.

Both modes expose an identical submit() / invalidate() / stop() interface.
"""

import logging
import multiprocessing
import os
import queue
import threading
import uuid
import os
import time
import multiprocessing
from typing import Any

from mkdb.db.query_workers.task import QueryTask
from mkdb.db.query_workers.worker import (
    WORKER_SENTINEL,
    worker_process_main,
    worker_thread_main,
)

logger = logging.getLogger(__name__)


class QueryDispatcher:
    """
    Owns the work queue, results queue, and the worker pool for one store.

    Parameters
    ----------
    store_name : str
    base_path  : str    Absolute path to the store root on disk.
    config     : query_worker_config
    """

    def __init__(self, store_name: str, base_path: str, config, store_config=None) -> None:
        self.store_name = store_name
        self.base_path  = base_path
        self.config     = config
        self.store_config_dict = store_config.json if store_config else {}

        # -- Shared queues ---------------------------------------------------
        # work_queue:    dispatcher → workers (all workers compete for items)
        # results_queue: workers → dispatcher result-router thread
        self._work_queue:    multiprocessing.Queue = multiprocessing.Queue()
        self._results_queue: multiprocessing.Queue = multiprocessing.Queue()

        # Per-worker private invalidation queues (one per worker so that a
        # single broadcast reaches every worker independently)
        self._invalidation_queues: list[multiprocessing.Queue] = []

        # -- Worker handles --------------------------------------------------
        self._workers: list[multiprocessing.Process | threading.Thread] = []

        # -- Stop signalling -------------------------------------------------
        # multiprocessing.Event works in both process and thread contexts.
        self._stop_event: multiprocessing.Event = multiprocessing.Event()

        # -- Pending-result tracking (main process / main thread only) -------
        self._pending_lock = threading.Lock()
        self._pending: dict[str, threading.Event] = {}   # task_id -> event
        self._results: dict[str, dict]            = {}   # task_id -> result dict

        # -- Worker stats tracking -------------------------------------------
        # worker_idx -> {completed, pid, last_active, is_alive}
        self._worker_stats: dict[int, dict] = {}

        # -- Result-router daemon thread -------------------------------------
        self._result_router: threading.Thread | None = None

        self._started = False
        self._last_restart_time = time.time()

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    def start(self) -> None:
        """Start the worker pool and the result-router thread."""
        if self._started:
            return
        self._started = True
        self._last_restart_time = time.time()
        self._stop_event.clear()

        # Always start the result-router thread (handles both modes)
        self._result_router = threading.Thread(
            target=self._result_router_loop,
            daemon=True,
            name=f"QueryDispatcher-Router[{self.store_name}]",
        )
        self._result_router.start()

        if self.config.parallel_enabled:
            self._start_process_workers()
        else:
            self._start_thread_worker()

        # Start reboot watcher if config is set
        if getattr(self.config, "reboot_interval_hours", 0) > 0:
            threading.Thread(
                target=self._reboot_watcher_loop,
                daemon=True,
                name=f"QueryDispatcher-RebootWatcher[{self.store_name}]"
            ).start()

        logger.info(
            "QueryDispatcher started for store '%s' | parallel=%s | workers=%d",
            self.store_name,
            self.config.parallel_enabled,
            len(self._workers),
        )

    def stop(self) -> None:
        """Gracefully stop all workers and the result-router."""
        if not self._started:
            return

        self._stop_event.set()

        # Send a sentinel for each live worker so they exit their blocking get()
        for _ in self._workers:
            self._work_queue.put(WORKER_SENTINEL)

        for w in self._workers:
            w.join(timeout=5.0)
            if hasattr(w, "is_alive") and w.is_alive():
                logger.warning("Worker %s did not exit cleanly; terminating", w.name)
                if isinstance(w, multiprocessing.Process):
                    w.terminate()

        self._workers.clear()
        self._invalidation_queues.clear()
        self._worker_stats.clear()
        self._started = False
        logger.info("QueryDispatcher stopped for store '%s'", self.store_name)

    def restart(self) -> None:
        """Shutdown and bring back up all workers."""
        logger.info("Restarting workers for store '%s'...", self.store_name)
        self.stop()
        self.start()

    def _reboot_watcher_loop(self) -> None:
        """Periodically checks if it's time to reboot workers."""
        while not self._stop_event.is_set():
            time.sleep(60) # check every minute
            interval = getattr(self.config, "reboot_interval_hours", 0)
            if interval <= 0:
                continue
            
            elapsed_hours = (time.time() - self._last_restart_time) / 3600.0
            if elapsed_hours >= interval:
                logger.info("Scheduled worker reboot for store '%s' (uptime: %.1f hours)", self.store_name, elapsed_hours)
                self.restart()
                # break for this thread as start() will spin up a new watcher if needed
                break

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def submit(self, operation: str, params: dict, timeout: float | None = None) -> Any:
        """
        Submit a query task and block until the worker returns a result.

        Parameters
        ----------
        operation : str   One of task.OPERATIONS.
        params    : dict  Operation-specific parameters (see QueryTask docstring).
        timeout   : float Seconds to wait. Defaults to config.task_timeout.

        Returns
        -------
        The resolved result (type depends on operation).

        Raises
        ------
        TimeoutError   If no result arrives within `timeout` seconds.
        RuntimeError   If the worker reports an error.
        """
        if not self._started:
            self.start()

        if timeout is None:
            timeout = float(self.config.task_timeout)

        task = QueryTask(operation=operation, store_name=self.store_name, params=params)
        event = threading.Event()

        with self._pending_lock:
            self._pending[task.task_id] = event

        self._work_queue.put(task.to_dict())

        if not event.wait(timeout=timeout):
            with self._pending_lock:
                self._pending.pop(task.task_id, None)
                self._results.pop(task.task_id, None)
            raise TimeoutError(
                f"Query task {task.task_id!r} (op={operation!r}) timed out after {timeout}s"
            )

        with self._pending_lock:
            result = self._results.pop(task.task_id)

        if result["status"] == "error":
            raise RuntimeError(result.get("error", "Unknown worker error"))

        return result["data"]

    def invalidate(self, record_id: str, metadata: Any = None) -> None:
        """
        Broadcast a cache-invalidation message to every worker.

        Called by the write queue after a record is flushed to disk so that
        stale entries are purged from all worker caches and the worker-local
        IndexManager is updated.
        """
        msg = {"id": record_id, "meta": metadata}
        for inv_q in self._invalidation_queues:
            try:
                inv_q.put_nowait(msg)
            except Exception:
                pass  # non-fatal if queue is full or closed

    # -----------------------------------------------------------------------
    # Observability
    # -----------------------------------------------------------------------

    @property
    def worker_count(self) -> int:
        return len(self._workers)

    @property
    def queue_depth(self) -> int:
        """Approximate number of unprocessed tasks in the work queue."""
        try:
            return self._work_queue.qsize()
        except NotImplementedError:
            # qsize() is not supported on macOS
            return -1

    @property
    def is_running(self) -> bool:
        return self._started and not self._stop_event.is_set()

    def status(self) -> dict:
        """Return a snapshot of worker pool status and stats."""
        status_data = {
            "store_name":     self.store_name,
            "parallel":       self.config.parallel_enabled,
            "worker_count":   len(self._workers),
            "queue_depth":    self.queue_depth,
            "pending_tasks":  len(self._pending),
            "running":        self.is_running,
            "workers":        []
        }
        
        for i, (w_idx, s) in enumerate(self._worker_stats.items()):
            w_handle = self._workers[i] if i < len(self._workers) else None
            is_alive = False
            if w_handle:
                is_alive = w_handle.is_alive()
            
            status_data["workers"].append({
                "id": w_idx,
                "pid": s["pid"],
                "type": s["type"],
                "completed": s["completed"],
                "last_active": s["last_active"],
                "is_alive": is_alive
            })
        
        return status_data

    # -----------------------------------------------------------------------
    # Internal — worker startup helpers
    # -----------------------------------------------------------------------

    def _start_process_workers(self) -> None:
        count = self.config.worker_count or (os.cpu_count() or 2)
        project_root = _find_project_root()

        for i in range(count):
            inv_q = multiprocessing.Queue()
            self._invalidation_queues.append(inv_q)

            p = multiprocessing.Process(
                target=worker_process_main,
                args=(
                    i,
                    project_root,
                    self.store_name,
                    self.base_path,
                    self._work_queue,
                    self._results_queue,
                    inv_q,
                    self.config.worker_cache_size,
                    float(self.config.worker_cache_ttl),
                    self._stop_event,
                    self.store_config_dict,
                ),
                daemon=True,
                name=f"QueryWorker[{self.store_name}#{i}]",
            )
            p.start()
            self._workers.append(p)
            self._worker_stats[i] = {
                "completed": 0,
                "pid": p.pid or 0,
                "last_active": 0.0,
                "type": "process"
            }

    def _start_thread_worker(self) -> None:
        inv_q = multiprocessing.Queue()
        self._invalidation_queues.append(inv_q)

        t = threading.Thread(
            target=worker_thread_main,
            args=(
                0,
                self.store_name,
                self.base_path,
                self._work_queue,
                self._results_queue,
                inv_q,
                self.config.worker_cache_size,
                float(self.config.worker_cache_ttl),
                self._stop_event,
                self.store_config_dict,
            ),
            daemon=True,
            name=f"QueryWorker[{self.store_name}#thread]",
        )
        t.start()
        self._workers.append(t)
        self._worker_stats[0] = {
            "completed": 0,
            "pid": os.getpid(),
            "last_active": 0.0,
            "type": "thread"
        }

    # -----------------------------------------------------------------------
    # Internal — result-router loop
    # -----------------------------------------------------------------------

    def _result_router_loop(self) -> None:
        """
        Daemon thread that reads from results_queue and wakes the correct waiter.

        Runs continuously until _stop_event is set and the results queue is drained.
        """
        while not self._stop_event.is_set():
            try:
                result = self._results_queue.get(timeout=0.5)
            except Exception:
                continue  # timeout — check stop_event and loop

            task_id = result.get("task_id")
            if not task_id:
                continue

            # Update worker stats
            worker_id = result.get("worker_id")
            if worker_id is not None and worker_id in self._worker_stats:
                stats = self._worker_stats[worker_id]
                stats["completed"] += 1
                stats["last_active"] = time.time()

            with self._pending_lock:
                self._results[task_id] = result
                event = self._pending.get(task_id)

            if event is not None:
                event.set()


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _find_project_root() -> str:
    """
    Walk up from this file's location to find the project root directory
    (the first ancestor that does NOT contain an __init__.py).
    Used to bootstrap sys.path in worker processes.
    """
    path = os.path.dirname(os.path.abspath(__file__))
    while True:
        parent = os.path.dirname(path)
        if parent == path:
            break
        if not os.path.exists(os.path.join(parent, "__init__.py")):
            return parent
        path = parent
    return path
