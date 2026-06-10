"""
HTTP data-plane request handler for MkDB.

Endpoints:
  POST   /data               — write / delta update a record
  GET    /data/{store}/{id}  — read a record
  DELETE /data/{store}/{id}  — delete a record
  POST   /query              — query by filter dict
  GET    /health             — server health check

All responses are JSON:
  success: {"status": "ok", "data": <result>}
  error:   {"status": "error", "message": "<text>", "code": <int>}
"""

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse

from mkdb.server.coms import metrics as _metrics
from mkdb.server.coms.actions import execute as _execute

logger = logging.getLogger(__name__)

MAX_BODY_SIZE = 10 * 1024 * 1024   # 10 MB default; overridden by config

# ---------------------------------------------------------------------------
# IP rate limiter state
# ---------------------------------------------------------------------------
_rate_tracker: dict = {}         # ip -> list[float] of recent request timestamps
_store_rate_tracker: dict = {}   # (ip, store_name) -> list[float]
_rate_lock = threading.Lock()
_rate_log_lock = threading.Lock()


def _log_rate_limit_event(base_path, ip: str, path: str, scope: str) -> None:
    """Append one JSON-lines record to logs/rate_limit.jsonl."""
    import os
    if not base_path:
        return
    try:
        log_dir = os.path.join(base_path, "logs")
        os.makedirs(log_dir, exist_ok=True)
        entry = json.dumps({
            "time":  time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "ip":    ip,
            "path":  path,
            "scope": scope,
        }) + "\n"
        with _rate_log_lock:
            with open(os.path.join(log_dir, "rate_limit.jsonl"), "a", encoding="utf-8") as f:
                f.write(entry)
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Token permission map
# format: token -> {store_name -> set of ops}
# e.g. {"secret123": {"products_1v": {"read", "write", "delete", "query"}}}
# When empty, all requests pass through (backwards-compatible).
# ---------------------------------------------------------------------------
_token_permissions: dict = {}   # token -> {store -> set[str]}


def register_token(token: str, store: str, ops: set) -> None:
    """
    Register an API token granting the given operations for a store.

    ops examples: {"read"}, {"write", "delete", "query"}, {"read", "write", "delete", "query"}
    """
    if not isinstance(ops, set):
        ops = set(ops)
    _token_permissions.setdefault(token, {})[store] = ops


# Operation required for each endpoint
_ENDPOINT_OP = {
    ("GET",    "data"):  "read",
    ("POST",   "data"):  "write",
    ("DELETE", "data"):  "delete",
    ("POST",   "query"): "query",
}


class HTTPDataHandler(BaseHTTPRequestHandler):
    """
    Data-plane HTTP handler. Attach the database reference via:
        HTTPDataHandler.database = my_mkdb_instance
    before passing to HTTPServer.
    """
    database = None

    # ------------------------------------------------------------------
    # Response helpers
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Security guards (rate limit + token auth)
    # ------------------------------------------------------------------

    def _check_rate_limit(self, store_name: str = None) -> bool:
        """
        Returns True (and sends 429) if any rate limit is exceeded.
        Checks global IP limit, then per-store limit when applicable.
        """
        db  = self.database
        ip  = self.client_address[0]
        now = time.time()
        cutoff = now - 1.0
        path = urlparse(self.path).path
        base_path = getattr(db.config, "base_path", None) if db else None

        # --- 1. Global limit (per IP across all endpoints) ---
        global_limit = 100
        if db is not None:
            global_limit = getattr(db.config.servers.http_server, "max_requests_per_second", 100)

        with _rate_lock:
            ts = _rate_tracker.get(ip, [])
            ts = [t for t in ts if t > cutoff]
            ts.append(now)
            _rate_tracker[ip] = ts
            global_count = len(ts)

        if global_count > global_limit:
            logger.warning("Rate limit (global) exceeded by %s on %s", ip, path)
            _log_rate_limit_event(base_path, ip, path, "global")
            if store_name:
                _metrics.record_rate_limited(store_name, ip, "http")
            self._json(429, {"status": "error", "message": "Rate limit exceeded", "code": 429})
            return True

        # --- 2. Per-store limit ---
        if store_name and db is not None:
            store_obj = db.stores.get(store_name)
            if store_obj is not None:
                rl = getattr(store_obj.config, "rate_limit", None)
                if rl is not None and getattr(rl, "enabled", False):
                    store_limit = getattr(rl, "max_requests_per_second", 100)
                    key = (ip, store_name)
                    with _rate_lock:
                        st = _store_rate_tracker.get(key, [])
                        st = [t for t in st if t > cutoff]
                        st.append(now)
                        _store_rate_tracker[key] = st
                        store_count = len(st)

                    if store_count > store_limit:
                        logger.warning(
                            "Rate limit (store: %s) exceeded by %s on %s",
                            store_name, ip, path
                        )
                        _log_rate_limit_event(base_path, ip, path, f"store:{store_name}")
                        _metrics.record_rate_limited(store_name, ip, "http")
                        self._json(429, {
                            "status":  "error",
                            "message": f"Rate limit exceeded for store '{store_name}'",
                            "code":    429,
                        })
                        return True

        return False

    def _check_token(self, path: str) -> bool:
        """
        Returns True if the request should be blocked (token missing or forbidden).
        Sends a 403 response and returns True; caller must return immediately.
        """
        # Skip when no tokens are registered (open mode)
        if not _token_permissions:
            return False

        raw_token = self.headers.get("Authorization", "")
        if raw_token.startswith("Bearer "):
            raw_token = raw_token[7:]
        raw_token = raw_token.strip()

        if not raw_token or raw_token not in _token_permissions:
            self._json(403, {"status": "error", "message": "Forbidden", "code": 403})
            return True

        # Determine required operation from method + first path segment
        parts = [p for p in path.split("/") if p]
        first_seg = parts[0] if parts else ""
        required_op = _ENDPOINT_OP.get((self.command, first_seg))
        if required_op is None:
            # Unknown endpoint — let routing handle the 404
            return False

        # Determine store name for path-based endpoints
        if first_seg == "data" and len(parts) >= 2:
            store_name = parts[1]
        else:
            # For POST /data and POST /query we can't read the body here;
            # use wildcard "*" to check if the token has any access at all
            store_name = "*"

        store_perms = _token_permissions[raw_token]
        # Accept if the token has a wildcard grant or a store-specific grant
        allowed = store_perms.get(store_name, set()) | store_perms.get("*", set())
        if required_op not in allowed:
            self._json(403, {"status": "error", "message": "Forbidden", "code": 403})
            return True
        return False

    def _check_user_auth(self, store_name: str = None, require_write: bool = False) -> bool:
        """
        Returns True (and sends 401/403) when user authentication fails.

        Skipped entirely when no users are configured (open/backwards-compat mode).
        For read requests: only checked when data_security.protect_reads is True.
        For write/delete requests: always checked when users are configured.
        """
        import base64
        db = self.database
        if db is None or not getattr(db.config, "users", {}):
            return False

        # Determine whether this request needs auth
        store_cfg = db.config.stores.get(store_name) if store_name else None
        protect_reads = getattr(store_cfg, "protect_reads", False) if store_cfg else False
        if not require_write and not protect_reads:
            # Read-only request and reads are not protected → allow
            return False

        auth_header = self.headers.get("Authorization", "")
        if not auth_header.startswith("Basic "):
            self._json(401, {"status": "error", "message": "Authentication required", "code": 401})
            return True

        try:
            raw = base64.b64decode(auth_header[6:]).decode("utf-8", errors="replace")
            colon = raw.index(":")
            username = raw[:colon]
            password = raw[colon + 1:]
        except Exception:
            self._json(401, {"status": "error", "message": "Invalid credentials format", "code": 401})
            return True

        from mkdb.server.control.server import verify_password
        user = db.config.users.get(username)
        if user is None or not verify_password(password, user.password_hash):
            self._json(403, {"status": "error", "message": "Invalid username or password", "code": 403})
            return True

        if store_name and store_name != "*":
            perm = user.stores.get(store_name)
            if perm is None:
                self._json(403, {"status": "error", "message": f"No access to store '{store_name}'", "code": 403})
                return True
            if require_write and not perm.write:
                self._json(403, {"status": "error", "message": "Write access denied", "code": 403})
                return True

        # Auth succeeded — track by username instead of IP
        self._client_key = username
        return False

    def _security_check(self) -> bool:
        """Run rate limit then token and user auth checks. Returns True if blocked."""
        # Default client identifier is the IP; overridden to username after auth
        self._client_key: str = self.client_address[0]
        path  = urlparse(self.path).path.rstrip("/")
        parts = [p for p in path.split("/") if p]

        # White-list the public client script
        if path == "/mkdb-client.js":
            return False

        # Extract store name and write requirement from URL (best-effort)
        store_name    = None
        require_write = False
        if parts and parts[0] == "data" and len(parts) >= 2:
            store_name    = parts[1]
            require_write = self.command in ("POST", "DELETE")

        if self._check_rate_limit(store_name):
            return True
        if path != "/health":
            if self._check_token(path):
                return True
            if self._check_user_auth(store_name, require_write):
                return True
        return False

    # ------------------------------------------------------------------
    # Response helpers
    # ------------------------------------------------------------------

    def _apply_cors_headers(self) -> None:
        db = self.database
        if db is None:
            return
        
        srv_cfg = getattr(db.config.servers, "http_server", None)
        if srv_cfg is None or not getattr(srv_cfg, "cors_enabled", False):
            return

        origin = self.headers.get("Origin")

        # 1. Access-Control-Allow-Origin
        # Default to * or matching origin from list
        cors_origins = getattr(srv_cfg, "cors_origins", ["*"])
        if "*" in cors_origins:
            # If credentials are allowed, origin cannot be *
            if getattr(srv_cfg, "cors_credentials", False) and origin:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            else:
                self.send_header("Access-Control-Allow-Origin", "*")
        elif origin and origin in cors_origins:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        
        # 2. Access-Control-Allow-Methods
        methods = getattr(srv_cfg, "cors_methods", ["GET", "POST", "DELETE", "OPTIONS"])
        self.send_header("Access-Control-Allow-Methods", ", ".join(methods))
        
        # 3. Access-Control-Allow-Headers
        headers = getattr(srv_cfg, "cors_headers", ["Content-Type", "Authorization", "X-Requested-With"])
        self.send_header("Access-Control-Allow-Headers", ", ".join(headers))
        
        # 4. Access-Control-Allow-Credentials
        if getattr(srv_cfg, "cors_credentials", False):
            self.send_header("Access-Control-Allow-Credentials", "true")
            
        # 5. Access-Control-Max-Age
        max_age = getattr(srv_cfg, "cors_max_age", 86400)
        self.send_header("Access-Control-Max-Age", str(max_age))

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self._apply_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _ok(self, data=None) -> None:
        self._json(200, {"status": "ok", "data": data})

    def _err(self, message: str, code: int = 400) -> None:
        self._json(code, {"status": "error", "message": message, "code": code})

    def _read_body(self) -> dict:
        db = self.database
        max_bytes = MAX_BODY_SIZE
        if db is not None:
            max_bytes = getattr(db.config.servers.http_server, "max_body_size", MAX_BODY_SIZE)
        length = int(self.headers.get("Content-Length", 0))
        if length > max_bytes:
            raise ValueError(f"Payload too large ({length} bytes, max {max_bytes})")
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._apply_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        if self._security_check():
            return
        path = urlparse(self.path).path.rstrip("/")

        if path == "/health":
            self._handle_health()
            return

        if path == "/mkdb-client.js":
            self._handle_serve_client()
            return

        # /data
        if path == "/data":
            self._handle_list_stores()
            return

        # /data/{store}/{id}
        parts = [p for p in path.split("/") if p]
        if len(parts) == 3 and parts[0] == "data":
            self._handle_read(parts[1], parts[2])
            return

        self._err("Not found", 404)

    def do_POST(self) -> None:
        if self._security_check():
            return
        path = urlparse(self.path).path.rstrip("/")

        if path == "/data":
            self._handle_write()
            return

        if path == "/query":
            self._handle_query()
            return

        self._err("Not found", 404)

    def do_DELETE(self) -> None:
        if self._security_check():
            return
        path = urlparse(self.path).path.rstrip("/")

        parts = [p for p in path.split("/") if p]
        if len(parts) == 3 and parts[0] == "data":
            self._handle_delete(parts[1], parts[2])
            return

        self._err("Not found", 404)

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _handle_health(self) -> None:
        db = self.database
        if db is None:
            self._ok({"status": "no database"})
            return
        self._ok({"status": "ok"})

    def _handle_serve_client(self) -> None:
        """Serves the standalone mkdb-client.js file with basic caching."""
        import os
        import hashlib
        # Path is relative to this file
        current_dir = os.path.dirname(os.path.abspath(__file__))
        client_path = os.path.join(current_dir, "..", "assets", "mkdb-client.js")
        
        if not os.path.exists(client_path):
            self._err("Client asset not found", 404)
            return

        with open(client_path, "rb") as f:
            content = f.read()

        # Generate ETag based on file content
        etag = f'"{hashlib.md5(content).hexdigest()}"'
        
        # Check If-None-Match header for 304 Not Modified
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.end_headers()
            return

        self.send_response(200)
        self._apply_cors_headers()
        self.send_header("Content-Type", "application/javascript; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "public, max-age=3600") # Cache for 1 hour
        self.end_headers()
        self.wfile.write(content)

    def _resolve_client_key(self, store_name: str) -> str:
        """Return the client identifier for metrics.

        Priority:
          1. Value of the store-configured header (if set and present in request)
          2. self._client_key (username after auth, or remote_addr)
        """
        db = self.database
        if db is not None and store_name and store_name in db.stores:
            header_name: str = getattr(db.stores[store_name].config, "client_id_header", "") or ""
            if header_name:
                val = (self.headers.get(header_name) or "").strip()
                if val:
                    return val
        return getattr(self, "_client_key", self.client_address[0])

    def _handle_read(self, store_name: str, record_id: str) -> None:
        r = _execute(self.database, "read", store_name, {"record_id": record_id},
                     self._resolve_client_key(store_name), "http")
        self._ok(r.data) if r.ok else self._err(r.error, r.http_code)

    def _handle_list_stores(self) -> None:
        r = _execute(self.database, "list_stores", "", {}, self._client_key, "http")
        self._ok(r.data) if r.ok else self._err(r.error, r.http_code)

    def _handle_write(self) -> None:
        try:
            data = self._read_body()
        except ValueError as exc:
            self._err(str(exc), 413 if "too large" in str(exc).lower() else 400)
            return
        except Exception as exc:
            self._err(f"Bad request: {exc}", 400)
            return

        r = _execute(
            self.database, "write",
            str(data.get("store", "")).strip(),
            {
                "record_id": str(data.get("record_id", "")).strip(),
                "delta":     data.get("delta", {}),
                "bytes_in":  int(self.headers.get("Content-Length", 0)),
            },
            self._resolve_client_key(str(data.get("store", "")).strip()), "http",
        )
        self._ok(r.data) if r.ok else self._err(r.error, r.http_code)

    def _handle_delete(self, store_name: str, record_id: str) -> None:
        r = _execute(self.database, "delete", store_name, {"record_id": record_id},
                     self._resolve_client_key(store_name), "http")
        self._ok(r.data) if r.ok else self._err(r.error, r.http_code)

    def _handle_query(self) -> None:
        try:
            data = self._read_body()
        except ValueError as exc:
            self._err(str(exc), 413 if "too large" in str(exc).lower() else 400)
            return
        except Exception as exc:
            self._err(f"Bad request: {exc}", 400)
            return

        r = _execute(
            self.database, "query",
            str(data.get("store", "")).strip(),
            data,
            self._resolve_client_key(str(data.get("store", "")).strip()), "http",
        )
        self._ok(r.data) if r.ok else self._err(r.error, r.http_code)

    def log_message(self, format: str, *args) -> None:
        """Suppress default stdout access log."""
        pass
