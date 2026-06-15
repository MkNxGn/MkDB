"""
SocketServer — persistent TCP server for MkDB data-plane operations.

Message framing: see socket_protocol.py (4-byte big-endian length prefix + JSON body)

Client → server message format:
{
  "type":      "request",
  "id":        "<uuid4 correlation ID>",
  "action":    "read" | "write" | "delete" | "query" | "subscribe" | "ping",
  "store":     "<store_name>",
  "record_id": "<id>",              # for read / write / delete
  "delta":     {<flat-path>: <v>},  # for write
  "filter":    {<query filter>},    # for query
}

Server → client response:
{
  "type":   "response",
  "id":     "<same correlation ID>",
  "status": "ok" | "error",
  "data":   <result> | None,
  "error":  <message> | None,
}

Subscribe message (client → server):
  {"type": "subscribe", "store": "<name>"}
  → server adds client to subscription set for that store

Broadcast event (server → subscribed clients):
  {"type": "update", "store": "<name>", "record_id": "<id>", "op": "write"|"delete"}
"""

import logging
import socket
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Optional

from mkdb.server.coms.socket_protocol import read_frame, write_frame, encode_message
from mkdb.server.coms.actions import execute as _execute

logger = logging.getLogger(__name__)

_MAX_RESPONSE_BYTES = 10 * 1024 * 1024  # must match client MAX_FRAME

# Default write password enforced when no users are configured in the DB.
_DEFAULT_WRITE_PASSWORD = "mk_db"


@dataclass
class ClientSession:
    addr:          tuple
    conn:          socket.socket
    connected_at:  float
    last_pong:     float
    authenticated: bool  = False
    username:      str   = ""      # empty = unauthenticated / open-mode
    can_read:      bool  = True    # True for R and RW sessions
    can_write:     bool  = False   # True for W and RW sessions after auth
    write_lock:    threading.Lock = None

    def __post_init__(self):
        self.write_lock = threading.Lock()


class SocketServer:
    def __init__(self, host: str, port: int, database, heartbeat_interval: float = 5.0,
                 max_clients: int = 100, recv_timeout: float = 30.0):
        self.host               = host
        self.port               = port
        self.database           = database
        self.heartbeat_interval = heartbeat_interval
        self.max_clients        = max_clients
        self.recv_timeout       = recv_timeout

        self._server_sock: Optional[socket.socket] = None
        self._clients:     dict  = {}    # addr_str -> ClientSession
        self._subscriptions: dict = {}   # store_name -> set of addr_str keys
        self._running:     bool  = False
        self._lock:        threading.Lock = threading.Lock()
        self._executor:    ThreadPoolExecutor = ThreadPoolExecutor(
            max_workers=max_clients * 2, thread_name_prefix="SocketServer-Worker"
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_sock.bind((self.host, self.port))
        self._server_sock.listen(self.max_clients)
        self._running = True
        logger.info("SocketServer listening on %s:%d", self.host, self.port)
        print(f"Socket server listening on {self.host}:{self.port}")

        accept_thread = threading.Thread(
            target=self._accept_loop, daemon=True, name="SocketServer-accept"
        )
        accept_thread.start()

    def stop(self) -> None:
        self._running = False
        self._executor.shutdown(wait=False)
        if self._server_sock:
            try:
                self._server_sock.close()
            except Exception:
                pass
        # Close all client connections
        with self._lock:
            for session in list(self._clients.values()):
                try:
                    write_frame(session.conn, {"type": "disconnect"})
                    session.conn.close()
                except Exception:
                    pass
            self._clients.clear()

    # ------------------------------------------------------------------
    # Accept loop
    # ------------------------------------------------------------------

    def _accept_loop(self) -> None:
        while self._running:
            try:
                conn, addr = self._server_sock.accept()
            except Exception:
                break
            with self._lock:
                if len(self._clients) >= self.max_clients:
                    try:
                        write_frame(conn, {"type": "error", "error": "Server full"})
                        conn.close()
                    except Exception:
                        pass
                    continue
            conn.settimeout(self.recv_timeout)
            thread = threading.Thread(
                target=self._client_loop, args=(conn, addr),
                daemon=True, name=f"SocketClient-{addr}"
            )
            thread.start()

    # ------------------------------------------------------------------
    # Client loop
    # ------------------------------------------------------------------

    # HTTP method prefixes — first 4 bytes of any HTTP/1.x request
    _HTTP_PREFIXES = {b"GET ", b"POST", b"PUT ", b"DELE", b"HEAD", b"PATC", b"OPTI"}

    def _client_loop(self, conn: socket.socket, addr: tuple) -> None:
        addr_str = f"{addr[0]}:{addr[1]}"
        now = time.time()
        session = ClientSession(addr=addr, conn=conn, connected_at=now, last_pong=now)

        with self._lock:
            self._clients[addr_str] = session

        logger.info("SocketServer: client connected %s", addr_str)

        # ── HTTP misrouting detection ─────────────────────────────────────────
        # Peek at the first 4 bytes without consuming them; if they look like
        # an HTTP method verb the client connected to the wrong port.
        # Use a short timeout: HTTP clients write immediately on connect, while
        # proper MkDB clients wait for the server's permissions frame first.
        try:
            conn.settimeout(0.15)
            first4 = conn.recv(4, socket.MSG_PEEK)
        except (socket.timeout, OSError):
            first4 = b""
        finally:
            conn.settimeout(self.recv_timeout)

        if first4 and first4 in self._HTTP_PREFIXES:
            db = self.database
            http_port = None
            if db is not None:
                http_port = getattr(
                    getattr(db.config, "servers", None),
                    "http_server", None,
                )
                http_port = getattr(http_port, "port", None)

            hint = (
                f"Use the HTTP data-plane port ({http_port})."
                if http_port else "Check your client configuration."
            )
            msg = (
                f"This port speaks the MkDB binary socket protocol, not HTTP. {hint}"
            )
            http_response = (
                "HTTP/1.1 400 Bad Request\r\n"
                "Content-Type: application/json\r\n"
                f"Content-Length: {len(msg) + 12}\r\n"
                "Connection: close\r\n\r\n"
                f'{{"error":"{msg}"}}'
            )
            try:
                conn.sendall(http_response.encode("utf-8"))
            except Exception:
                pass
            self._unregister(addr_str)
            try:
                conn.close()
            except Exception:
                pass
            logger.warning("SocketServer: rejected HTTP client at %s — wrong port", addr_str)
            return

        # ── Handshake ─────────────────────────────────────────────────────────
        # 1. Server sends PERMISSIONS message describing what needs auth.
        # 2. Client replies with their desired access level: "R" or "RW".
        # 3. If auth is required for that level, server challenges with AUTH.
        # 4. Client sends {type: "auth", username: ..., password: ...}.
        db = self.database
        has_users     = db is not None and bool(getattr(db.config, "users", {}))
        
        # Determine if any read operations are protected (globally or per-store)
        global_protect = db is not None and getattr(db.config.data_security, "protect_reads", False)
        store_protect  = (
            db is not None
            and any(getattr(sc, "protect_reads", False) for sc in db.config.stores.values())
        )
        protect_reads = has_users and (global_protect or store_protect)

        try:
            self._safe_write(session, {
                "type":           "permissions",
                "read_protected":  protect_reads,
                # Writes are always protected — real users or the default password
                "write_protected": True,
            })

            # Wait for the client's access-level declaration: "R", "W", or "RW"
            access_msg = read_frame(conn)
            if not access_msg:
                return
            client_access = str(access_msg.get("access", "R")).upper()
            if client_access not in ("R", "W", "RW"):
                self._safe_write(session, {"type": "error", "error": "access must be 'R', 'W', or 'RW'"})
                return

            # Writes always need auth (real users, or default password if none configured).
            # Reads need auth only when protect_reads is True.
            need_auth = client_access in ("W", "RW") or (protect_reads and client_access == "R")

            if need_auth:
                self._safe_write(session, {"type": "auth_required"})
                auth_msg = read_frame(conn)
                if not auth_msg or auth_msg.get("type") != "auth":
                    self._safe_write(session, {"type": "error", "error": "Expected auth message"})
                    return
                username = str(auth_msg.get("username", ""))
                password = str(auth_msg.get("password", ""))

                if has_users:
                    from mkdb.server.control.server import verify_password
                    user = db.config.users.get(username)
                    if user is None or not verify_password(password, user.password_hash):
                        self._safe_write(session, {"type": "error", "error": "Invalid credentials"})
                        return
                    session.username = username
                else:
                    # No users configured — enforce the default write password
                    # unless it has been explicitly disabled in config.
                    ds = getattr(getattr(db, "config", None), "data_security", None)
                    if getattr(ds, "disable_default_password", False):
                        self._safe_write(session, {"type": "error", "error": "Write access disabled — configure users to enable writes"})
                        return
                    if password != _DEFAULT_WRITE_PASSWORD:
                        self._safe_write(session, {"type": "error", "error": "Invalid credentials"})
                        return

                session.authenticated = True
                session.can_read      = client_access in ("R", "RW")
                session.can_write     = client_access in ("W", "RW")
                self._safe_write(session, {
                    "type":      "auth_ok",
                    "username":  username,
                    "can_read":  session.can_read,
                    "can_write": session.can_write,
                })
                logger.info("SocketServer: %s authenticated as '%s' (access=%s)",
                            addr_str, username, client_access)
            else:
                # Read-only, no auth required
                session.authenticated = True
                session.can_read      = True
                session.can_write     = False
                self._safe_write(session, {"type": "ready", "can_read": True, "can_write": False})

        except (ConnectionError, OSError) as exc:
            logger.warning("SocketServer: handshake error with %s: %s", addr_str, exc)
            self._unregister(addr_str)
            try:
                conn.close()
            except Exception:
                pass
            return

        # ── Start heartbeat ───────────────────────────────────────────────────
        hb_thread = threading.Thread(
            target=self._heartbeat_loop, args=(conn, addr_str, session),
            daemon=True, name=f"SocketHB-{addr_str}"
        )
        hb_thread.start()

        try:
            while self._running:
                try:
                    msg = read_frame(conn)
                except (ConnectionError, OSError, ValueError) as exc:
                    if isinstance(exc, ValueError):
                        logger.warning("SocketServer: oversized frame from %s: %s", addr_str, exc)
                        try:
                            write_frame(conn, {"type": "error", "error": str(exc)})
                        except Exception:
                            pass
                    break

                if not msg:
                    continue

                msg_type = msg.get("type")

                # Handle pong
                if msg_type == "pong":
                    session.last_pong = time.time()
                    continue

                # Handle subscribe
                if msg_type == "subscribe":
                    store_name = msg.get("store", "")
                    with self._lock:
                        self._subscriptions.setdefault(store_name, set()).add(addr_str)
                    if not self._safe_write(session, {"type": "subscribed", "store": store_name}):
                        break
                    continue

                # Handle requests
                if msg_type == "request":
                    correlation_id = msg.get("id")
                    
                    # 1. Immediate ACK (Receipt)
                    if correlation_id:
                        self._safe_write(session, {"type": "receipt", "id": correlation_id})
                    
                    # 2. Dispatch to worker pool
                    self._executor.submit(self._process_background_request, msg, session)
                    continue

        finally:
            self._unregister(addr_str)
            try:
                conn.close()
            except Exception:
                pass
            logger.info("SocketServer: client disconnected %s", addr_str)

    def _process_background_request(self, msg: dict, session: ClientSession) -> None:
        """Executed in a worker thread to keep the main read loop free."""
        try:
            response = self._handle_request(msg, session)
            
            # Check for oversized response
            frame = encode_message(response)
            if len(frame) > _MAX_RESPONSE_BYTES:
                response = {
                    "type":   "response",
                    "id":     response.get("id"),
                    "status": "error",
                    "data":   None,
                    "error":  (
                        f"Response too large ({len(frame):,} bytes > "
                        f"{_MAX_RESPONSE_BYTES:,} byte limit). "
                        "Use hydrate=False or apply more specific filters."
                    ),
                }
            
            if not self._safe_write(session, response):
                logger.debug("Failed to send response to %s (disconnected)", session.addr)
        except Exception as exc:
            logger.error("Error processing request from %s: %s", session.addr, exc)

    # ------------------------------------------------------------------
    # Heartbeat
    # ------------------------------------------------------------------

    def _heartbeat_loop(self, conn: socket.socket, addr_str: str, session: ClientSession) -> None:
        while self._running and addr_str in self._clients:
            time.sleep(self.heartbeat_interval)
            if addr_str not in self._clients:
                break
            if not self._safe_write(session, {"type": "ping"}):
                break
            # Check pong timeout
            if time.time() - session.last_pong > 2 * self.heartbeat_interval:
                logger.warning("SocketServer: client %s timed out (no pong)", addr_str)
                self._unregister(addr_str)
                try:
                    conn.close()
                except Exception:
                    pass
                break

    # ------------------------------------------------------------------
    # Broadcast (pub-sub)
    # ------------------------------------------------------------------

    def broadcast(self, store_name: str, event: dict) -> None:
        """Fan-out an event to all subscribers for a store."""
        with self._lock:
            subscribers = set(self._subscriptions.get(store_name, set()))
        dead = set()
        for addr_str in subscribers:
            session = self._clients.get(addr_str)
            if session is None:
                dead.add(addr_str)
                continue
            if not self._safe_write(session, event):
                dead.add(addr_str)
        # Clean up dead subscribers
        if dead:
            with self._lock:
                subs = self._subscriptions.get(store_name, set())
                subs -= dead

    # ------------------------------------------------------------------
    # Request dispatch
    # ------------------------------------------------------------------

    def _handle_request(self, msg: dict, session: ClientSession) -> dict:
        correlation_id = msg.get("id", str(uuid.uuid4()))
        action     = msg.get("action")
        store_name = msg.get("store", "")

        def ok(data=None):
            return {"type": "response", "id": correlation_id, "status": "ok", "data": data, "error": None}

        def err(message: str):
            return {"type": "response", "id": correlation_id, "status": "error", "data": None, "error": message}

        if not action:
            return err("Missing 'action' field")

        db = self.database
        if db is None:
            return err("Database not available")

        # ── Permission checks ────────────────────────────────────────────────
        is_write = action in ("write", "delete")

        # Session-level capability gates (set during handshake)
        if is_write and not session.can_write:
            return err("Write access denied — reconnect with 'W' or 'RW' access")
        if action in ("read", "query") and not session.can_read:
            return err("Read access denied — reconnect with 'R' or 'RW' access")

        has_users = bool(getattr(db.config, "users", {}))
        if has_users and session.username:
            user = db.config.users.get(session.username)
            if user is None:
                return err("Authenticated user no longer exists")
            if store_name:
                perm = user.stores.get(store_name)
                if perm is None:
                    return err(f"No access to store '{store_name}'")
                if is_write and not perm.write:
                    return err("Write access denied")
                if not is_write and not getattr(perm, "read", True):
                    return err("Read access denied")
        elif has_users and is_write:
            return err("Authentication required for write operations")
        elif has_users and not is_write and action in ("read", "query"):
            store_cfg = db.config.stores.get(store_name)
            
            global_protect = getattr(db.config.data_security, "protect_reads", False)
            store_protect  = getattr(store_cfg, "protect_reads", False) if store_cfg else False
            
            if (global_protect or store_protect) and not session.username:
                return err(f"Authentication required to read from store '{store_name}'")

        # ── Execute ──────────────────────────────────────────────────────────
        client_key = session.username or session.addr[0]
        params = {
            "record_id": msg.get("record_id", ""),
            "delta":     msg.get("delta", {}),
            "filter":    msg.get("filter", {}),
            "hydrate":   bool(msg.get("hydrate", False)),
            "sort":      msg.get("sort"),
            "limit":     msg.get("limit"),
            "offset":    msg.get("offset"),
        }
        r = _execute(db, action, store_name, params, client_key, "socket",
                     on_broadcast=self.broadcast)
        return ok(r.data) if r.ok else err(r.error)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _unregister(self, addr_str: str) -> None:
        with self._lock:
            self._clients.pop(addr_str, None)
            for subs in self._subscriptions.values():
                subs.discard(addr_str)

    def _safe_write(self, session: ClientSession, payload: dict) -> bool:
        """Send a frame safely using the session's write lock."""
        try:
            frame = encode_message(payload)
            with session.write_lock:
                session.conn.sendall(frame)
            return True
        except Exception:
            return False
