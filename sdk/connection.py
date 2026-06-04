"""
Low-level persistent TCP connection to a MkDB socket server.

Wire format: 4-byte big-endian uint32 length + UTF-8 JSON payload.
Mirrors src/server/coms/socket_protocol.py (client-side copy).
"""

import json
import socket
import struct
import threading
import uuid
from typing import Callable, Optional


MAX_FRAME = 10 * 1024 * 1024   # 10 MB


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Connection closed")
        buf += chunk
    return buf


def _encode(payload: dict) -> bytes:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return struct.pack(">I", len(body)) + body


def _decode(data: bytes) -> dict:
    return json.loads(data.decode("utf-8"))


class Connection:
    """
    Thread-safe persistent connection to MkDB.

    Outbound: _send() serialises and writes to socket.
    Inbound:  background _reader_loop() dispatches to registered handlers.
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
        self._pending: dict[str, threading.Event] = {}     # correlation_id -> Event
        self._results: dict[str, dict] = {}                # correlation_id -> response dict
        self._push_handlers: list[Callable[[dict], None]] = []   # for server-push events
        self._running  = False
        self.can_read  = False
        self.can_write = False

    # ------------------------------------------------------------------
    # Connect / disconnect
    # ------------------------------------------------------------------

    def connect(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self.recv_timeout)
        sock.connect((self.host, self.port))

        # ── Handshake ────────────────────────────────────────────────
        perm = self._handshake_recv(sock)
        if perm.get("type") != "permissions":
            sock.close()
            raise ConnectionError(f"Expected 'permissions', got: {perm}")

        read_protected  = perm.get("read_protected", False)
        write_protected = perm.get("write_protected", False)

        # Declare desired access level
        self._handshake_send(sock, {"access": self._access})

        need_auth = (
            (self._access in ("W", "RW") and write_protected)
            or (self._access == "R" and read_protected)
        )

        if need_auth:
            challenge = self._handshake_recv(sock)
            if challenge.get("type") != "auth_required":
                sock.close()
                raise ConnectionError(f"Expected 'auth_required', got: {challenge}")
            if not self._password:
                sock.close()
                raise PermissionError("Server requires authentication but no password provided")
            self._handshake_send(sock, {
                "type":     "auth",
                "username": self._username,
                "password": self._password,
            })
            result = self._handshake_recv(sock)
            if result.get("type") == "error":
                sock.close()
                raise PermissionError(result.get("error", "Authentication failed"))
            if result.get("type") != "auth_ok":
                sock.close()
                raise ConnectionError(f"Unexpected handshake response: {result}")
            self.can_read  = result.get("can_read",  False)
            self.can_write = result.get("can_write", False)
        else:
            ready = self._handshake_recv(sock)
            if ready.get("type") == "error":
                sock.close()
                raise ConnectionError(ready.get("error", "Connection refused"))
            self.can_read  = ready.get("can_read",  True)
            self.can_write = ready.get("can_write", False)

        self._sock    = sock
        self._running = True
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
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
        self._sock = None

    # ------------------------------------------------------------------
    # Send / receive
    # ------------------------------------------------------------------

    def send(self, payload: dict) -> dict:
        """
        Send a request and block until the matching response arrives.
        Returns the response dict.
        """
        correlation_id = str(uuid.uuid4())
        payload["id"] = correlation_id
        payload.setdefault("type", "request")

        event = threading.Event()
        with self._lock:
            self._pending[correlation_id] = event

        frame = _encode(payload)
        with self._lock:
            self._sock.sendall(frame)

        event.wait(timeout=self.recv_timeout)
        with self._lock:
            result = self._results.pop(correlation_id, None)
            self._pending.pop(correlation_id, None)
        if result is None:
            raise TimeoutError("No response received within timeout")
        return result

    def send_raw(self, payload: dict) -> None:
        """Fire-and-forget (used for subscribe)."""
        frame = _encode(payload)
        with self._lock:
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
            except Exception:
                break

            msg_type = msg.get("type")
            correlation_id = msg.get("id")

            if msg_type == "response" and correlation_id:
                with self._lock:
                    event = self._pending.get(correlation_id)
                    if event:
                        self._results[correlation_id] = msg
                        event.set()
            elif msg_type == "ping":
                # Respond with pong
                try:
                    self._sock.sendall(_encode({"type": "pong"}))
                except Exception:
                    break
            else:
                # Server-push: dispatch to registered handlers
                for handler in self._push_handlers:
                    try:
                        handler(msg)
                    except Exception:
                        pass
