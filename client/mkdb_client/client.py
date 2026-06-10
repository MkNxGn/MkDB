"""
MkDB high-level SDK client.

Usage:
    from mkdb_client import MkDBClient

    client = MkDBClient(host="127.0.0.1", port=9001)
    client.connect()

    client.set("products_1v", "p001", {"name": "Widget", "price": 9.99})
    record = client.get("products_1v", "p001")
    print(record)

    results = client.query("products_1v", {"price": {"gte": 5, "lte": 20}})
    print(results)

    client.on_update("products_1v", lambda event: print("Change:", event))
    client.close()
"""

import hmac
import hashlib
import secrets
from typing import Any, Callable, Optional, Union

from .connection import Connection
from .http_connection import HttpConnection
from .query_builder import Q
from .responses import (
    GetResponse,
    WriteResponse,
    ListStoresResponse,
    GenerateIdResponse,
    SnapshotBaseObject,
)
from .exceptions import (
    MkDBServerError,
    MkDBStoreNotFoundError,
    MkDBRecordNotFoundError,
    MkDBStoreExistsError,
    MkDBQueryError,
)
from .responses import (
    GetResponse,
    WriteResponse,
    DeleteResponse,
    QueryResponse,
    GenerateIdResponse,
    ListStoresResponse,
)


class MkDBClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 9001,
                 access: str = "R",
                 username: str = "",
                 password: str = "mk_db",
                 recv_timeout: float = 30.0,
                 transport: str = "socket",
                 track_records: bool = False):
        """
        Parameters
        ----------
        host, port      : server address
        access          : "R", "W", or "RW"
        username        : username (required if server has users configured)
        password        : password / write key
        recv_timeout    : seconds to wait for a response
        transport       : "socket" (default) or "http"
                          HTTP transport does not support pub-sub (on_update).
        track_records   : if True, .get() returns a SnapshotBaseObject that supports .patch()
        """
        self.track_records = track_records
        t = transport.lower()
        if t == "http":
            self._conn = HttpConnection(host=host, port=port, recv_timeout=recv_timeout,
                                        access=access, username=username, password=password)
        else:
            self._conn = Connection(host=host, port=port, recv_timeout=recv_timeout,
                                    access=access, username=username, password=password)
        self.auth = AuthManager(self)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> None:
        self._conn.connect()

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------
    # Data operations
    # ------------------------------------------------------------------

    @property
    def query_builder(self) -> Q:
        """Get a new Q object for building queries."""
        return Q()

    def get(self, store: str, record_id: str, as_type: Optional[type] = None) -> GetResponse:
        """Read a record. Returns a GetResponse (check .found before using .data)."""
        resp = self._conn.send({
            "action": "read",
            "store":  store,
            "record_id": record_id,
        })
        self._raise_if_error(resp)
        data = resp.get("data")

        # Determine wrap type: explicitly passed, or client-level default
        wrap_type = as_type
        if wrap_type is None and self.track_records:
            wrap_type = SnapshotBaseObject

        obj = data
        if data is not None and wrap_type:
            # Create instance first
            obj = wrap_type(data)
            # Inject metadata directly if it's a SnapshotBaseObject (or subclass)
            if hasattr(obj, "bind") and callable(obj.bind):
                obj.bind(store=store, record_id=record_id, client=self)
            elif isinstance(obj, dict):
                # If they passed dict as the wrap_type for some reason, ignore binding
                pass
            else:
                # Fallback for other objects: try direct attribute setting
                setattr(obj, "__store__", store)
                setattr(obj, "__id__", record_id)
                setattr(obj, "__client__", self)

        return GetResponse(record_id=record_id, data=obj, found=data is not None)

    def list_stores(self) -> ListStoresResponse:
        """List all stores on the server."""
        resp = self._conn.send({
            "action": "list_stores",
            "store": ""
        })
        self._raise_if_error(resp)
        return ListStoresResponse(stores=resp.get("data", []))

    def get_async(self, store: str, record_id: str, as_type: Optional[type] = None):
        """Asynchronously read a record."""
        task = self._conn.send_async({
            "action": "read",
            "store":  store,
            "record_id": record_id,
        })
        original_result = task.result
        def wrapped_result(timeout=None):
            resp = original_result(timeout)
            self._raise_if_error(resp)
            data = resp.get("data")
            
            # Determine wrap type
            wrap_type = as_type
            if wrap_type is None and self.track_records:
                wrap_type = SnapshotBaseObject
                
            obj = data
            if data is not None and wrap_type:
                # Create instance first
                obj = wrap_type(data)
                # Inject metadata directly if it's a SnapshotBaseObject (or subclass)
                if hasattr(obj, "bind") and callable(obj.bind):
                    obj.bind(store=store, record_id=record_id, client=self)
                else:
                    setattr(obj, "__store__", store)
                    setattr(obj, "__id__", record_id)
                    setattr(obj, "__client__", self)

            return GetResponse(record_id=record_id, data=obj, found=data is not None)
        task.result = wrapped_result
        return task

    def set(self, store: str, record_id: str, delta: dict) -> WriteResponse:
        """Write / update a record. Returns a WriteResponse."""
        resp = self._conn.send({
            "action":    "write",
            "store":     store,
            "record_id": record_id,
            "delta":     delta,
        })
        self._raise_if_error(resp)
        rid = (resp.get("data") or {}).get("record_id", record_id)
        return WriteResponse(record_id=rid, store=store)

    def set_async(self, store: str, record_id: str, delta: dict):
        """Asynchronously write / update a record."""
        task = self._conn.send_async({
            "action":    "write",
            "store":     store,
            "record_id": record_id,
            "delta":     delta,
        })
        original_result = task.result
        def wrapped_result(timeout=None):
            resp = original_result(timeout)
            self._raise_if_error(resp)
            rid = (resp.get("data") or {}).get("record_id", record_id)
            return WriteResponse(record_id=rid, store=store)
        task.result = wrapped_result
        return task

    def insert(self, store: str, delta: dict) -> WriteResponse:
        """Write a new record with a server-generated ID. Returns a WriteResponse."""
        resp = self._conn.send({
            "action": "write",
            "store":  store,
            "delta":  delta,
        })
        self._raise_if_error(resp)
        rid = (resp.get("data") or {}).get("record_id", "")
        return WriteResponse(record_id=rid, store=store)

    def generate_id(self, store: str) -> GenerateIdResponse:
        """Ask the server to generate a unique ID for the store without writing anything."""
        resp = self._conn.send({
            "action": "generate_id",
            "store":  store,
        })
        self._raise_if_error(resp)
        rid = (resp.get("data") or {}).get("record_id", "")
        return GenerateIdResponse(record_id=rid, store=store)

    def delete(self, store: str, record_id: str) -> DeleteResponse:
        """Delete a record. Returns a DeleteResponse."""
        resp = self._conn.send({
            "action":    "delete",
            "store":     store,
            "record_id": record_id,
        })
        self._raise_if_error(resp)
        return DeleteResponse(record_id=record_id, store=store)

    def query(self, store: str, filter_dict: Union[dict, Q],
              hydrate: bool = False,
              sort: Optional[str] = None,
              limit: Optional[int] = None,
              offset: Optional[int] = None) -> QueryResponse:
        """Query a store. Returns a QueryResponse."""
        if isinstance(filter_dict, Q):
            # Extract parameters from Q object
            built = filter_dict.build()
            payload = {
                "action": "query",
                "store": store,
                "filter": built.get("filter", {}),
                "hydrate": hydrate,
                "sort": sort or built.get("sort"),
                "limit": limit or built.get("limit"),
                "offset": offset or built.get("offset")
            }
        else:
            payload = {
                "action":  "query",
                "store":   store,
                "filter":  filter_dict,
                "hydrate": hydrate,
                "sort":    sort,
                "limit":   limit,
                "offset":  offset,
            }

        resp = self._conn.send(payload)
        self._raise_if_error(resp)
        data = resp.get("data") or {}

        return QueryResponse(
            count=data.get("count", 0),
            total_matches=data.get("total_matches", data.get("count", 0)),
            ids=data.get("ids", []),
            records=data.get("records"),
            store=store
        )

    def query_async(self, store: str, filter_dict: Union[dict, Q],
                    hydrate: bool = False,
                    sort: Optional[str] = None,
                    limit: Optional[int] = None,
                    offset: Optional[int] = None):
        """
        Start a query asynchronously. Returns a MkDBTask object.
        You can call .result() on the returned task to wait for the QueryResponse.
        """
        if isinstance(filter_dict, Q):
            built = filter_dict.build()
            payload = {
                "action": "query",
                "store": store,
                "filter": built.get("filter", {}),
                "hydrate": hydrate,
                "sort": sort or built.get("sort"),
                "limit": limit or built.get("limit"),
                "offset": offset or built.get("offset")
            }
        else:
            payload = {
                "action":  "query",
                "store":   store,
                "filter":  filter_dict,
                "hydrate": hydrate,
                "sort":    sort,
                "limit":   limit,
                "offset":  offset,
            }

        task = self._conn.send_async(payload)
        
        # We wrap the task's result so it returns a QueryResponse instead of a raw dict
        def wrapped_result(timeout=None):
            resp = task.result(timeout)
            self._raise_if_error(resp)
            data = resp.get("data") or {}
            
            return QueryResponse(
                count=data.get("count", 0),
                total_matches=data.get("total_matches", data.get("count", 0)),
                ids=data.get("ids", []),
                records=data.get("records"),
                store=store
            )
        
        task.result = wrapped_result
        return task

    # ------------------------------------------------------------------
    # Pub-sub
    # ------------------------------------------------------------------

    def on_update(self, store: str, callback: Callable[[dict], None]) -> None:
        """
        Subscribe to updates for a store and register a callback for push events.
        The callback receives the full server-push event dict.
        """
        def _filter(event: dict) -> None:
            if event.get("store") == store:
                callback(event)
        self._conn.register_push_handler(_filter)
        self._conn.send_raw({"type": "subscribe", "store": store})

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _raise_if_error(resp: dict) -> None:
        if resp.get("status") == "ok":
            return
        msg = resp.get("error") or "Unknown error"
        low = msg.lower()
        if "not found in store" in low or "record" in low and "not found" in low:
            raise MkDBRecordNotFoundError(msg)
        if "store" in low and "not found" in low:
            raise MkDBStoreNotFoundError(msg)
        if "already exists" in low:
            raise MkDBStoreExistsError(msg)
        if "syntax" in low or "invalid query" in low or "filter" in low or "'filter' must" in low:
            raise MkDBQueryError(msg)
        raise MkDBServerError(msg)


class AuthManager:
    """
    Simple credential store using MkDB itself as the backing store.

    Passwords are never stored in plaintext.
    Storage format (flat dict in MkDB):
      {"auth.salt": "<hex>", "auth.hash": "<sha256 hex>"}
    Stored under store="__auth__", record_id=username.
    """

    AUTH_STORE = "__auth__"

    def __init__(self, client: MkDBClient):
        self._client = client

    def register(self, username: str, password: str) -> None:
        """
        Register a new user. Raises RuntimeError if username already exists
        or if the __auth__ store is unavailable.
        """
        salt = secrets.token_hex(32)
        pw_hash = hashlib.sha256(
            (password + salt).encode("utf-8")
        ).hexdigest()
        self._client.set(
            self.AUTH_STORE,
            username,
            {"auth.salt": salt, "auth.hash": pw_hash},
            flatten_nested=False,
        )

    def validate(self, username: str, password: str) -> bool:
        """
        Validate credentials. Returns True if valid, False otherwise.
        Uses hmac.compare_digest to prevent timing attacks.
        """
        record = self._client.get(self.AUTH_STORE, username)
        if record is None:
            # Use compare_digest anyway to avoid timing oracle
            hmac.compare_digest("x", "y")
            return False
        stored_salt = record.get("auth.salt", "")
        stored_hash = record.get("auth.hash", "")
        computed = hashlib.sha256(
            (password + stored_salt).encode("utf-8")
        ).hexdigest()
        return hmac.compare_digest(computed, stored_hash)
