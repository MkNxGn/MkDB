"""
event_log.py — In-memory circular event log for the MkDB dashboard.

Usage:
    from mkdb.server.event_log import emit, get_events

    emit("info",    "store:products", "Store loaded — 1024 entries recovered from disk")
    emit("warning", "store:orders",   "RAM cache full — eviction started")
    emit("error",   "db",             "File recovery attempted on segment orders_003.log")
"""

import threading
import time
from typing import Literal

_MAX_EVENTS = 200          # circular buffer depth
_lock        = threading.Lock()
_events: list[dict] = []   # newest at the end

Level = Literal["info", "warning", "error", "recovery"]


def emit(level: Level, source: str, message: str) -> None:
    """Append one event to the ring buffer, dropping the oldest if full."""
    entry = {
        "ts":      round(time.time(), 3),
        "level":   level,
        "source":  source,
        "message": message,
    }
    with _lock:
        _events.append(entry)
        if len(_events) > _MAX_EVENTS:
            _events.pop(0)


def get_events(limit: int = 50, level: str = "") -> list[dict]:
    """Return up to `limit` most-recent events (newest first).

    Parameters
    ----------
    limit : int
        Maximum number of events to return (capped at _MAX_EVENTS).
    level : str
        If given, filter to only events with this level.
    """
    with _lock:
        snapshot = list(_events)          # copy under lock
    snapshot.reverse()                    # newest first
    if level:
        snapshot = [e for e in snapshot if e["level"] == level]
    return snapshot[:max(1, min(limit, _MAX_EVENTS))]


def clear() -> None:
    """Wipe all events (used by tests / admin reset)."""
    with _lock:
        _events.clear()
