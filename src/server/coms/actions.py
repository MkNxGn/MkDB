"""
actions.py — Shared data-plane action executor for MkDB.

Both the HTTP and socket transports delegate here for the actual
read / write / delete / query logic so nothing is duplicated.

Usage
-----
from src.server.coms.actions import execute, ActionResult

result = execute(database, "read", store_name, {"record_id": rid},
                 client_key="alice", transport="http")
if result.ok:
    send_response(result.data)
else:
    send_error(result.error, result.http_code)
"""

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from src.server.coms import metrics as _metrics

logger = logging.getLogger(__name__)


@dataclass
class ActionResult:
    """Return value from execute()."""
    ok:        bool
    data:      Any  = None   # payload on success
    error:     str  = ""     # human-readable message on failure
    http_code: int  = 400    # suggested HTTP status code on failure (socket ignores this)


def execute(
    database,
    action:     str,
    store_name: str,
    params:     dict,
    client_key: str,
    transport:  str,
    *,
    on_broadcast: Optional[Callable[[str, dict], None]] = None,
) -> ActionResult:
    """
    Execute a data-plane action and return an ActionResult.

    Parameters
    ----------
    database      : active mkdb database instance
    action        : "ping" | "read" | "write" | "delete" | "query"
    store_name    : target store name (empty string valid only for "ping")
    params        : action-specific values —
                      read   → record_id (str)
                      write  → record_id (str, optional), delta (dict), bytes_in (int, optional)
                      delete → record_id (str)
                      query  → filter (dict), hydrate (bool)
    client_key    : username or IP, forwarded to metrics
    transport     : "http" | "socket", forwarded to metrics
    on_broadcast  : optional callback(store_name, event_dict) for pub-sub (used by socket)
    """
    if database is None:
        return ActionResult(ok=False, error="Database not available", http_code=503)

    if action == "ping":
        return ActionResult(ok=True, data="pong")

    # All non-ping actions require a named store.
    if not store_name:
        return ActionResult(ok=False, error="'store' is required", http_code=400)
    store_obj = database.stores.get(store_name)
    if store_obj is None:
        return ActionResult(ok=False, error=f"Store '{store_name}' not found", http_code=404)

    try:
        if action == "read":
            return _do_read(store_obj, store_name, params, client_key, transport)
        elif action == "write":
            return _do_write(store_obj, store_name, params, client_key, transport, on_broadcast)
        elif action == "delete":
            return _do_delete(store_obj, store_name, params, client_key, transport, on_broadcast)
        elif action == "query":
            return _do_query(store_obj, store_name, params, client_key, transport)
        else:
            return ActionResult(ok=False, error=f"Unknown action: {action!r}", http_code=400)
    except Exception as exc:
        logger.error("Action %r on store %r failed: %s", action, store_name, exc, exc_info=True)
        _metrics.record(store_name, "error", client_key, transport=transport,
                        error_msg=f"{type(exc).__name__}: {exc}")
        return ActionResult(ok=False, error=str(exc), http_code=500)


# ---------------------------------------------------------------------------
# Per-action implementations
# ---------------------------------------------------------------------------

def _do_read(store_obj, store_name: str, params: dict, client_key: str, transport: str) -> ActionResult:
    record_id = params.get("record_id", "")
    if not record_id:
        return ActionResult(ok=False, error="Missing 'record_id'", http_code=400)

    result = store_obj.read(record_id)
    if result is None:
        _metrics.record(store_name, "error", client_key, transport=transport,
                        error_msg=f"Record '{record_id}' not found in store '{store_name}'")
        return ActionResult(
            ok=False,
            error=f"Record '{record_id}' not found in store '{store_name}'",
            http_code=404,
        )
    bytes_out = len(json.dumps(result).encode())
    _metrics.record(store_name, "read", client_key, bytes_out=bytes_out, transport=transport)
    return ActionResult(ok=True, data=result)


def _do_write(store_obj, store_name: str, params: dict, client_key: str, transport: str,
              on_broadcast) -> ActionResult:
    record_id = str(params.get("record_id", "")).strip()
    delta     = params.get("delta", {})
    bytes_in  = int(params.get("bytes_in", 0))

    if not isinstance(delta, dict):
        return ActionResult(ok=False, error="'delta' must be a JSON object", http_code=400)

    if not record_id:
        try:
            record_id = store_obj.generate_id()
        except RuntimeError as exc:
            return ActionResult(ok=False, error=str(exc), http_code=503)

    # Fall back to measuring the delta when the transport didn't supply a byte count.
    if not bytes_in:
        bytes_in = len(json.dumps(delta).encode())

    store_obj.write(record_id, delta)
    _metrics.record(store_name, "write", client_key, bytes_in=bytes_in, transport=transport)

    if on_broadcast:
        on_broadcast(store_name, {
            "type": "update", "store": store_name,
            "record_id": record_id, "op": "write",
        })
    return ActionResult(ok=True, data={"record_id": record_id})


def _do_delete(store_obj, store_name: str, params: dict, client_key: str, transport: str,
               on_broadcast) -> ActionResult:
    record_id = params.get("record_id", "")
    if not record_id:
        return ActionResult(ok=False, error="Missing 'record_id'", http_code=400)

    store_obj.delete(record_id)
    _metrics.record(store_name, "delete", client_key, transport=transport)

    if on_broadcast:
        on_broadcast(store_name, {
            "type": "update", "store": store_name,
            "record_id": record_id, "op": "delete",
        })
    return ActionResult(ok=True, data={"record_id": record_id})


def _do_query(store_obj, store_name: str, params: dict, client_key: str, transport: str) -> ActionResult:
    filter_dict = params.get("filter", {})
    hydrate     = bool(params.get("hydrate", False))

    if not isinstance(filter_dict, dict):
        return ActionResult(ok=False, error="'filter' must be a JSON object", http_code=400)

    qe = getattr(store_obj, "query_engine", None)
    if qe is None:
        return ActionResult(ok=False, error=f"Store '{store_name}' has no query engine", http_code=500)

    t0 = time.monotonic()
    ids = qe.query(filter_dict)
    duration_ms = (time.monotonic() - t0) * 1000.0

    if hydrate:
        records = [store_obj.read(rid) for rid in ids]
        result  = {"count": len(records), "records": records}
    else:
        result  = {"count": len(ids), "ids": ids}

    bytes_out = len(json.dumps(result).encode())
    _metrics.record(store_name, "query", client_key, bytes_out=bytes_out, transport=transport)

    # Slow query detection
    threshold_ms = getattr(getattr(store_obj, "config", None), "slow_query_threshold_ms", 0.0)
    if threshold_ms > 0 and duration_ms >= threshold_ms:
        _metrics.record_slow_query(
            store_name, duration_ms, filter_dict,
            result.get("count", 0), client_key, transport,
        )
        try:
            from src.server.event_log import emit as _emit
            _emit(
                "warning", f"store:{store_name}",
                f"Slow query ({duration_ms:.1f} ms ≥ {threshold_ms:.0f} ms threshold): "
                f"filter={json.dumps(filter_dict)}",
            )
        except Exception:
            pass

    result["duration_ms"] = round(duration_ms, 2)
    return ActionResult(ok=True, data=result)
