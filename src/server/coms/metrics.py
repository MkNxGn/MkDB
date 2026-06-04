"""
metrics.py — In-memory per-store, per-client request and bandwidth tracking.

All operations are thread-safe via a single RLock.

Structure
---------
_store_metrics[store_name] = {
    "reads":         int,    # total read requests
    "writes":        int,    # total write requests
    "deletes":       int,    # total delete requests
    "queries":       int,    # total query requests
    "errors":        int,    # total errored requests
    "rate_limited":  int,    # times this store was rate-limited
    "bytes_in":      int,    # bytes received in request bodies (writes)
    "bytes_out":     int,    # bytes sent in response bodies (reads/queries)
    "started_at":    float,  # unix timestamp of first metric recorded
    "transport":     {       # breakdown by transport ("http" / "socket")
        "http":   {"reads": 0, "writes": 0, ...},
        "socket": {"reads": 0, "writes": 0, ...},
    },
    "clients": {
        "<username or ip>": {
            "reads": int, "writes": int, "deletes": int, "queries": int,
            "errors": int, "rate_limited": int, "bytes_in": int, "bytes_out": int,
        },
        ...
    },
    "rate_limited_ips": {         # ip -> count of times blocked for this store
        "<ip>": int,
    },
}
"""

import threading
import time
from collections import deque

_lock = threading.RLock()

# store_name -> metric dict
_store_metrics: dict = {}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _client_default() -> dict:
    return {
        "reads": 0, "writes": 0, "deletes": 0, "queries": 0,
        "errors": 0, "rate_limited": 0, "bytes_in": 0, "bytes_out": 0,
    }


def _transport_default() -> dict:
    return {
        "http":   _client_default(),
        "socket": _client_default(),
    }


def _store_default() -> dict:
    return {
        "reads": 0, "writes": 0, "deletes": 0, "queries": 0,
        "errors": 0, "rate_limited": 0,
        "bytes_in": 0, "bytes_out": 0,
        "slow_queries": 0,
        "started_at": time.time(),
        "transport": _transport_default(),
        "clients": {},
        "rate_limited_ips": {},
        "slow_query_log": deque(maxlen=50),
        "error_log": deque(maxlen=50),
    }


def _get_store(store_name: str) -> dict:
    """Return (and lazily create) the metric dict for a store."""
    if store_name not in _store_metrics:
        _store_metrics[store_name] = _store_default()
    return _store_metrics[store_name]


def _get_client(store_metrics: dict, client_key: str) -> dict:
    """Return (and lazily create) the client sub-dict."""
    clients = store_metrics["clients"]
    if client_key not in clients:
        clients[client_key] = _client_default()
    return clients[client_key]


# ---------------------------------------------------------------------------
# Public recording API
# ---------------------------------------------------------------------------

def record(
    store_name: str,
    op: str,                    # "read" | "write" | "delete" | "query" | "error"
    client_key: str = "",       # username or IP
    bytes_in:  int = 0,
    bytes_out: int = 0,
    transport: str = "http",    # "http" | "socket"
    error_msg: str = "",        # human-readable error message (only used when op="error")
) -> None:
    """
    Record a single operation against a store.

    Parameters
    ----------
    store_name  : str   — name of the store
    op          : str   — one of "read", "write", "delete", "query", "error"
    client_key  : str   — username (if authenticated) or IP address
    bytes_in    : int   — bytes received in the request body
    bytes_out   : int   — bytes sent in the response body
    transport   : str   — "http" or "socket"
    """
    if not store_name:
        return
    op = op.lower()
    _op_to_field = {"read": "reads", "write": "writes", "delete": "deletes",
                    "query": "queries", "error": "errors"}
    field = _op_to_field.get(op, op)

    with _lock:
        sm = _get_store(store_name)
        sm[field]      = sm.get(field, 0) + 1
        sm["bytes_in"]  += bytes_in
        sm["bytes_out"] += bytes_out

        # Transport breakdown
        t = transport if transport in ("http", "socket") else "http"
        td = sm["transport"].setdefault(t, _client_default())
        td[field]      = td.get(field, 0) + 1
        td["bytes_in"]  += bytes_in
        td["bytes_out"] += bytes_out

        # Per-client breakdown
        if client_key:
            cd = _get_client(sm, client_key)
            cd[field]       = cd.get(field, 0) + 1
            cd["bytes_in"]  += bytes_in
            cd["bytes_out"] += bytes_out

        # Error log ring buffer
        if op == "error" and error_msg:
            sm["error_log"].append({
                "ts":        time.time(),
                "message":   error_msg,
                "client":    client_key,
                "transport": transport,
            })


def record_slow_query(
    store_name: str,
    duration_ms: float,
    filter_dict: dict,
    result_count: int,
    client_key: str = "",
    transport: str = "http",
) -> None:
    """Append a slow-query entry to the store's ring buffer and increment counter."""
    if not store_name:
        return
    entry = {
        "ts":           time.time(),
        "duration_ms":  round(duration_ms, 2),
        "filter":       filter_dict,
        "result_count": result_count,
        "client":       client_key,
        "transport":    transport,
    }
    with _lock:
        sm = _get_store(store_name)
        sm["slow_queries"] = sm.get("slow_queries", 0) + 1
        sm["slow_query_log"].append(entry)


def record_rate_limited(    store_name: str,
    ip: str,
    transport: str = "http",
) -> None:
    """Record a rate-limit block event for a store and IP."""
    if not store_name:
        return
    with _lock:
        sm = _get_store(store_name)
        sm["rate_limited"] = sm.get("rate_limited", 0) + 1
        rl_ips = sm.setdefault("rate_limited_ips", {})
        rl_ips[ip] = rl_ips.get(ip, 0) + 1

        t = transport if transport in ("http", "socket") else "http"
        td = sm["transport"].setdefault(t, _client_default())
        td["rate_limited"] = td.get("rate_limited", 0) + 1

        # Also record as a client entry so the per-IP table shows it
        cd = _get_client(sm, ip)
        cd["rate_limited"] = cd.get("rate_limited", 0) + 1


# ---------------------------------------------------------------------------
# Read API
# ---------------------------------------------------------------------------

def get_store_metrics(store_name: str) -> dict:
    """Return a snapshot of all metrics for a store (deep-copied)."""
    import copy
    with _lock:
        if store_name not in _store_metrics:
            return _store_default()
        return copy.deepcopy(_store_metrics[store_name])


def get_all_metrics() -> dict:
    """Return a snapshot of metrics for every store."""
    import copy
    with _lock:
        return copy.deepcopy(_store_metrics)


def reset_store_metrics(store_name: str) -> None:
    """Clear all metrics for a store."""
    with _lock:
        if store_name in _store_metrics:
            _store_metrics[store_name] = _store_default()


def all_store_names() -> list:
    with _lock:
        return list(_store_metrics.keys())
