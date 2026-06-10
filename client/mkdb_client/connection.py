"""
Low-level persistent TCP connection to a MkDB socket server.

Wire format: 4-byte big-endian uint32 length + UTF-8 JSON payload.
Mirrors src/server/coms/socket_protocol.py (client-side copy).
"""

import json
import logging
import socket
import struct
import threading
import time
import uuid
from typing import Callable, Optional, Dict, Any

from .exceptions import MkDBAuthError, MkDBConnectionError, MkDBTimeoutError

logger = logging.getLogger(__name__)

MAX_FRAME = 10 * 1024 * 1024   # 10 MB
RECONNECT_INTERVAL = 10.0       # seconds between reconnect attempts


class MkDBTask:
    """A 'Future'-like object for tracking an asynchronous request."""
    def __init__(self, correlation_id: str, timeout: float):
        self.id = correlation_id
        self.timeout = timeout
        self.created_at = time.time()
        
        self.receipt_event = threading.Event()
        self.response_event = threading.Event()
        
        self.response = None
        self.error = None

    def wait_for_receipt(self, timeout: Optional[float] = None) -> bool:
        """Wait for the server to acknowledge receipt of the message."""
        return self.receipt_event.wait(timeout or 1.0) # Default fast ACK

    def result(self, timeout: Optional[float] = None) -> Any:
        """Wait for and return the final response data."""
        t = timeout or self.timeout
        if not self.response_event.wait(t):
            raise MkDBTimeoutError(f"Request {self.id} timed out after {t}s")
        if self.error:
            raise MkDBConnectionError(self.error)
        return self.response


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Connection closed")
        buf += chunk
    return buf


def _encode(payload: dict) -> bytes:
    body = json.dumps(payload, ensure_ascii=False, default=_json_default).encode("utf-8")
    return struct.pack(">I", len(body)) + body


def _decode(data: bytes) -> dict:
    return json.loads(data.decode("utf-8"))


def _json_default(obj):
    if isinstance(obj, set):
        return list(obj)
    raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")


class Connection:
    """
    Thread-safe persistent connection to MkDB.

    Outbound: _send() serialises and writes to socket.
    Inbound:  background _reader_loop() dispatches to registered handlers.

    Automatically reconnects after a disconnection (checked every
    RECONNECT_INTERVAL seconds).  Active store subscriptions are
    re-registered after each successful reconnect.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 9001,
                 recv_timeout: float = 30.0,
                 access: str = "R",
                 username: str = "",
                 password: str = ""):
        self.host         = host
        self.port         = port
        self.recv_timeout = recv_timeout
        self._access      = access.upper() if access.upper() in ("R", "W", "RW") else "R"
        self._username    = username
        self._password    = password
        self._sock: Optional[socket.socket] = None
        self._lock        = threading.Lock()
        self._write_lock  = threading.Lock()
        self._pending: dict[str, MkDBTask] = {}            # correlation_id -> Task
        self._push_handlers: list[Callable[[dict], None]] = []
        self._subscriptions: set[str] = set()              # stores with active subscriptions
        self._running          = False   # False → intentional close, stop watchdog
        self._reconnect_lock   = threading.Lock()
        self.last_receive_at   = 0.0
        self.can_read  = False
        self.can_write = False

    # ------------------------------------------------------------------
    # Connect / disconnect
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Connect and start the background reader + watchdog threads."""
        self._running = True
        self._connect_socket()
        watchdog = threading.Thread(
            target=self._watchdog_loop, daemon=True, name="MkDB-SDK-watchdog"
        )
        watchdog.start()

    def _connect_socket(self) -> None:
        """Open a fresh TCP socket, run the handshake, start a reader thread."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self.recv_timeout)
        sock.connect((self.host, self.port))

        # ── Handshake ────────────────────────────────────────────────
        perm = self._handshake_recv(sock)
        if perm.get("type") != "permissions":
            sock.close()
            raise MkDBConnectionError(f"Expected 'permissions', got: {perm}")

        read_protected  = perm.get("read_protected", False)
        write_protected = perm.get("write_protected", False)

        self._handshake_send(sock, {"access": self._access})

        need_auth = (
            (self._access in ("W", "RW") and write_protected)
            or (self._access == "R" and read_protected)
        )

        if need_auth:
            challenge = self._handshake_recv(sock)
            if challenge.get("type") != "auth_required":
                sock.close()
                raise MkDBConnectionError(f"Expected 'auth_required', got: {challenge}")
            if not self._password:
                sock.close()
                raise MkDBAuthError("Server requires authentication but no password provided")
            self._handshake_send(sock, {
                "type":     "auth",
                "username": self._username,
                "password": self._password,
            })
            result = self._handshake_recv(sock)
            if result.get("type") == "error":
                sock.close()
                raise MkDBAuthError(result.get("error", "Authentication failed"))
            if result.get("type") != "auth_ok":
                sock.close()
                raise MkDBConnectionError(f"Unexpected handshake response: {result}")
            self.can_read  = result.get("can_read",  False)
            self.can_write = result.get("can_write", False)
        else:
            ready = self._handshake_recv(sock)
            if ready.get("type") == "error":
                sock.close()
                raise MkDBConnectionError(ready.get("error", "Connection refused"))
            self.can_read  = ready.get("can_read",  True)
            self.can_write = ready.get("can_write", False)

        self._sock = sock
        reader = threading.Thread(
            target=self._reader_loop, daemon=True, name="MkDB-SDK-reader"
        )
        reader.start()

    @staticmethod
    def _handshake_send(sock: socket.socket, msg: dict) -> None:
        body = json.dumps(msg).encode("utf-8")
        sock.sendall(struct.pack(">I", len(body)) + body)

    @staticmethod
    def _handshake_recv(sock: socket.socket) -> dict:
        hdr    = _recv_exact(sock, 4)
        length = struct.unpack(">I", hdr)[0]
        return json.loads(_recv_exact(sock, length).decode("utf-8"))

    def close(self) -> None:
        """Permanently close the connection. Disables automatic reconnect."""
        self._running = False
        sock, self._sock = self._sock, None
        if sock:
            try:
                sock.close()
            except Exception:
                pass
        # Wake any pending requests so they don't hang
        with self._lock:
            for event in self._pending.values():
                event.set()

    # ------------------------------------------------------------------
    # Send / receive
    # ------------------------------------------------------------------

    def send(self, payload: dict, _retry: bool = True) -> dict:
        """
        Send a request and block until the matching response arrives.
        Includes a fast 1s timeout for the 'receipt' ACK to detect dead lines.
        """
        task = self.send_async(payload, _retry=_retry)
        
        # 1. Wait for receipt (confirms server got it)
        if not task.wait_for_receipt(timeout=1.0):
            # If server didn't even ACK, the connection might be dead
            if _retry and self._running:
                logger.warning("No receipt for %s, trying reconnect", task.id)
                self._try_reconnect()
                payload.pop("id", None)
                return self.send(payload, _retry=False)
            raise MkDBConnectionError("Server failed to acknowledge request")

        # 2. Wait for final result
        return task.result()

    def send_async(self, payload: dict, _retry: bool = True) -> MkDBTask:
        """
        Send a request and return a Task object immediately.
        """
        if self._sock is None:
            if _retry and self._running:
                self._try_reconnect()
            if self._sock is None:
                raise MkDBConnectionError("Not connected to MkDB server")

        correlation_id = payload.get("id") or str(uuid.uuid4())
        payload["id"] = correlation_id
        payload.setdefault("type", "request")

        task = MkDBTask(correlation_id, self.recv_timeout)
        with self._lock:
            self._pending[correlation_id] = task

        frame = _encode(payload)
        try:
            with self._write_lock:
                if self._sock is None:
                    raise MkDBConnectionError("Not connected to MkDB server")
                self._sock.sendall(frame)
        except Exception as exc:
            with self._lock:
                self._pending.pop(correlation_id, None)
            if _retry and self._running:
                self._try_reconnect()
                return self.send_async(payload, _retry=False)
            raise MkDBConnectionError(f"Send failed: {exc}") from exc

        return task

    def send_raw(self, payload: dict) -> None:
        """Fire-and-forget (used for subscribe). Tracks subscriptions for reconnect."""
        if payload.get("type") == "subscribe":
            store = payload.get("store")
            if store:
                self._subscriptions.add(store)

        if self._sock is None:
            return   # will be re-sent by watchdog after reconnect
        frame = _encode(payload)
        with self._write_lock:
            if self._sock:
                self._sock.sendall(frame)

    def register_push_handler(self, handler: Callable[[dict], None]) -> None:
        """Register a callback for server-push (ping, update, subscribed, disconnect)."""
        self._push_handlers.append(handler)

    # ------------------------------------------------------------------
    # Reader loop (background thread)
    # ------------------------------------------------------------------

    def _reader_loop(self) -> None:
        while self._running and self._sock:
            try:
                length_bytes = _recv_exact(self._sock, 4)
                length = struct.unpack(">I", length_bytes)[0]
                if length > MAX_FRAME:
                    break   # protocol violation — disconnect
                payload_bytes = _recv_exact(self._sock, length)
                msg = _decode(payload_bytes)
                self.last_receive_at = time.time()
            except Exception:
                break

            msg_type = msg.get("type")
            correlation_id = msg.get("id")

            if correlation_id:
                with self._lock:
                    task = self._pending.get(correlation_id)
                
                if task:
                    if msg_type == "receipt":
                        task.receipt_event.set()
                    elif msg_type == "response":
                        task.response = msg
                        task.receipt_event.set() # Also set receipt if we skipped it
                        task.response_event.set()
                        with self._lock:
                            self._pending.pop(correlation_id, None)
            
            if msg_type == "ping":
                try:
                    with self._write_lock:
                        if self._sock:
                            self._sock.sendall(_encode({"type": "pong"}))
                except Exception:
                    break
            elif msg_type not in ("receipt", "response"):
                # Broadcast or other push message
                for handler in self._push_handlers:
                    try:
                        handler(msg)
                    except Exception:
                        pass

        # Reader exiting — clean up socket reference
        old_sock, self._sock = self._sock, None
        if old_sock:
            try:
                old_sock.close()
            except Exception:
                pass

        # Wake all pending requests so they fail fast instead of hanging
        with self._lock:
            for task in self._pending.values():
                task.error = "Connection closed"
                task.receipt_event.set()
                task.response_event.set()
            self._pending.clear()

    # ------------------------------------------------------------------
    # Watchdog / auto-reconnect
    # ------------------------------------------------------------------

    def _watchdog_loop(self) -> None:
        """Check every RECONNECT_INTERVAL seconds and reconnect if dropped."""
        while self._running:
            time.sleep(RECONNECT_INTERVAL)
            if not self._running:
                break
            
            # Dead-line detection: if we haven't heard anything in 2x timeout, force close
            if self._sock and self.last_receive_at > 0:
                if time.time() - self.last_receive_at > (self.recv_timeout * 2):
                    logger.warning("MkDB: connection timed out (no data for %ds), forcing reconnect", 
                                   time.time() - self.last_receive_at)
                    try:
                        self._sock.close()
                    except:
                        pass
                    self._sock = None

            if self._sock is None:
                self._try_reconnect()

    def _try_reconnect(self) -> None:
        with self._reconnect_lock:
            if self._sock is not None or not self._running:
                return   # already reconnected or intentionally closed
            try:
                logger.info("MkDB: attempting reconnect to %s:%s", self.host, self.port)
                self._connect_socket()
                logger.info("MkDB: reconnected successfully")
                # Re-register store subscriptions
                for store in list(self._subscriptions):
                    try:
                        frame = _encode({"type": "subscribe", "store": store})
                        with self._write_lock:
                            if self._sock:
                                self._sock.sendall(frame)
                    except Exception:
                        pass
            except Exception as exc:
                logger.warning("MkDB: reconnect failed: %s", exc)

