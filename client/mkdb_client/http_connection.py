"""
HTTP transport for the MkDB SDK.

Implements the same interface that Connection does (send / close / can_read /
can_write / register_push_handler) but uses plain HTTP requests against the
MkDB HTTP data-plane server instead of the persistent socket protocol.

Limitations vs. socket transport:
- No server-push / pub-sub (register_push_handler is a no-op).
- Each call opens a new HTTP request (no persistent connection).
- `send_raw` is a no-op (subscribe is not supported).
"""

import base64
import json
import urllib.request
import urllib.error
from typing import Callable, Optional

from .exceptions import MkDBConnectionError, MkDBTransportError


class HttpConnection:
    """HTTP client that mirrors the Connection interface."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 80,
        recv_timeout: float = 30.0,
        access: str = "RW",
        username: str = "",
        password: str = "",
    ):
        self.host         = host
        self.port         = port
        self.recv_timeout = recv_timeout
        self._access      = access.upper()
        self._username    = username
        self._password    = password

        # Mirrors Connection public attributes
        self.can_read  = "R" in self._access
        self.can_write = "W" in self._access

        self._base_url  = f"http://{host}:{port}"
        self._auth_header: Optional[str] = None  # populated in connect()

    # ------------------------------------------------------------------
    # Lifecycle (no persistent connection needed for HTTP)
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Build auth header if credentials are provided; verify reachability."""
        if self._username and self._password:
            raw = f"{self._username}:{self._password}"
            encoded = base64.b64encode(raw.encode()).decode()
            self._auth_header = f"Basic {encoded}"
        # Optional: hit /health to verify the server is up
        try:
            self._http_get("/health")
        except Exception as exc:
            raise MkDBConnectionError(f"MkDB HTTP server unreachable at {self._base_url}: {exc}") from exc

    def close(self) -> None:
        """No persistent socket to close."""
        pass

    # ------------------------------------------------------------------
    # Send (translate socket-style payload to HTTP calls)
    # ------------------------------------------------------------------

    def send(self, payload: dict) -> dict:
        """
        Translate a socket-style request dict into the appropriate HTTP call.

        Supported actions: ping, read, write, delete, query.
        """
        action     = payload.get("action", "")
        store      = payload.get("store", "")
        record_id  = payload.get("record_id", "")
        delta      = payload.get("delta", {})
        filter_d   = payload.get("filter", {})
        hydrate    = payload.get("hydrate", False)

        if action == "ping":
            data = self._http_get("/health")
            return {"type": "response", "status": "ok", "data": data}

        if action == "read":
            if not store or not record_id:
                return self._err("'store' and 'record_id' are required for read")
            data = self._http_get(f"/data/{store}/{record_id}")
            return {"type": "response", "status": "ok", "data": data}

        if action == "write":
            body = {"store": store, "record_id": record_id, "delta": delta}
            data = self._http_post("/data", body)
            return {"type": "response", "status": "ok", "data": data}

        if action == "delete":
            if not store or not record_id:
                return self._err("'store' and 'record_id' are required for delete")
            data = self._http_delete(f"/data/{store}/{record_id}")
            return {"type": "response", "status": "ok", "data": data}

        if action == "query":
            body = {"store": store, "filter": filter_d, "hydrate": hydrate}
            data = self._http_post("/query", body)
            return {"type": "response", "status": "ok", "data": data}

        return self._err(f"Action '{action}' is not supported over HTTP transport")

    def send_raw(self, payload: dict) -> None:
        """No-op — pub-sub subscribe is not available over HTTP."""
        pass

    def register_push_handler(self, handler: Callable[[dict], None]) -> None:
        """No-op — server-push is not available over HTTP."""
        pass

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _headers(self, content_type: bool = False) -> dict:
        h = {}
        if self._auth_header:
            h["Authorization"] = self._auth_header
        if content_type:
            h["Content-Type"] = "application/json; charset=utf-8"
        return h

    def _http_get(self, path: str) -> dict:
        url = self._base_url + path
        req = urllib.request.Request(url, headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=self.recv_timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                return body.get("data", body)
        except urllib.error.HTTPError as exc:
            body = {}
            try:
                body = json.loads(exc.read().decode("utf-8"))
            except Exception:
                pass
            raise MkDBTransportError(body.get("message", str(exc))) from exc

    def _http_post(self, path: str, payload: dict) -> dict:
        url = self._base_url + path
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=self._headers(content_type=True), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.recv_timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                return body.get("data", body)
        except urllib.error.HTTPError as exc:
            body = {}
            try:
                body = json.loads(exc.read().decode("utf-8"))
            except Exception:
                pass
            raise MkDBTransportError(body.get("message", str(exc))) from exc

    def _http_delete(self, path: str) -> dict:
        url = self._base_url + path
        req = urllib.request.Request(url, headers=self._headers(), method="DELETE")
        try:
            with urllib.request.urlopen(req, timeout=self.recv_timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                return body.get("data", body)
        except urllib.error.HTTPError as exc:
            body = {}
            try:
                body = json.loads(exc.read().decode("utf-8"))
            except Exception:
                pass
            raise MkDBTransportError(body.get("message", str(exc))) from exc

    @staticmethod
    def _err(message: str) -> dict:
        return {"type": "response", "status": "error", "error": message}
