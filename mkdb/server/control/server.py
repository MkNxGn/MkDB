import hashlib
import json
import os
import secrets
import time
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse

from mkdb.filing import read_file
from mkdb.server.coms.http import HTTPServer

# ── Session store (in-memory, cleared on restart) ────────────────────────────
_SESSIONS: dict[str, float] = {}         # admin cookie token → expiry
_SDK_SESSIONS: dict[str, str] = {}       # sdk bearer token → control_username


# ── Password helpers ──────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    """Hash a password using PBKDF2-HMAC-SHA256 with a random salt."""
    salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260_000)
    return f"pbkdf2:sha256:260000:{salt}:{key.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Verify a plaintext password against a stored PBKDF2 hash."""
    if not stored:
        return False
    try:
        _, algo, iters, salt, key_hex = stored.split(":")
        key = hashlib.pbkdf2_hmac(algo, password.encode(), salt.encode(), int(iters))
        return secrets.compare_digest(key.hex(), key_hex)
    except Exception:
        return False


# ── Session helpers ───────────────────────────────────────────────────────────

def _new_session(ttl: int) -> str:
    token = secrets.token_hex(32)
    _SESSIONS[token] = time.time() + ttl
    _purge_expired()
    return token


def _purge_expired() -> None:
    now = time.time()
    for t in [t for t, exp in _SESSIONS.items() if now > exp]:
        del _SESSIONS[t]


def _validate_session(token: str) -> bool:
    if not token:
        return False
    expiry = _SESSIONS.get(token)
    if expiry is None:
        return False
    if time.time() > expiry:
        del _SESSIONS[token]
        return False
    return True


def invalidate_all_sessions() -> int:
    count = len(_SESSIONS)
    _SESSIONS.clear()
    return count


def active_session_count() -> int:
    _purge_expired()
    return len(_SESSIONS)


# ── SDK / control-user session helpers ───────────────────────────────────────

def _new_sdk_token(username: str) -> str:
    token = "sdk_" + secrets.token_hex(32)
    _SDK_SESSIONS[token] = username
    return token


def _validate_sdk_token(token: str):
    """Return username or None."""
    return _SDK_SESSIONS.get(token)


# ── Role-based permission map ─────────────────────────────────────────────────
# Maps action name → minimum role required ("viewer" < "operator" < "admin").
# Any action NOT listed requires "admin" (safe default).

_ROLE_ORDER = {"viewer": 0, "operator": 1, "admin": 2}

_ACTION_ROLES: dict[str, str] = {
    # viewer
    "api_list_stores":            "viewer",
    "api_get_store_config":       "viewer",
    "api_export_store_json":      "viewer",
    "api_get_db_info":            "viewer",
    "api_get_dashboard":          "viewer",
    "api_get_event_log":          "viewer",
    "api_get_auth_status":        "viewer",
    "api_get_db_settings":        "viewer",
    "api_get_server_status":      "viewer",
    "api_list_users":             "viewer",
    "api_list_control_users":     "viewer",
    "api_get_rate_limit_log":     "viewer",
    "api_get_store_metrics":      "viewer",
    "api_get_all_store_metrics":  "viewer",
    # operator
    "api_create_store":              "operator",
    "api_delete_store":              "operator",
    "api_update_store":              "operator",
    "api_update_store_config":       "operator",
    "api_reset_store_metrics":       "operator",
    "api_discover_store_fields":     "operator",
    "api_rebuild_store_indexes":     "operator",
    "api_create_user":               "operator",
    "api_delete_user":               "operator",
    "api_set_user_password":         "operator",
    "api_set_user_store_access":     "operator",
    "api_remove_user_store_access":  "operator",
    # admin
    "api_create_control_user":    "admin",
    "api_delete_control_user":    "admin",
    "api_set_control_user_password": "admin",
    "api_set_control_user_role":  "admin",
    "api_update_server_config":   "admin",
    "api_server_control":         "admin",
    "api_set_auth_password":      "admin",
    "api_set_session_ttl":        "admin",
    "api_invalidate_sessions":    "admin",
    "api_set_data_security":      "admin",
    "api_update_db_settings":     "admin",
    # admin — everything else defaults to admin
}


# ── Request handler ───────────────────────────────────────────────────────────

class HTTPRequestHandler(BaseHTTPRequestHandler):
    database = None   # set by start_control_server

    # helpers ─────────────────────────────────────────────────────────────────

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, code: int, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.end_headers()

    def _read_body(self, max_bytes: int = 1_048_576) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length > max_bytes:
            raise ValueError(f"Payload too large ({length} bytes, max {max_bytes})")
        raw = self.rfile.read(length)
        return json.loads(raw.decode())

    def _serve_file(self, name: str) -> None:
        # Sanitise: strip path separators to prevent traversal
        safe_name = os.path.basename(name)
        file_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "files", f"{safe_name}.html"
        )
        if os.path.exists(file_path):
            self._html(200, read_file(file_path).encode())
        else:
            self._html(404, b"<h1>404 Not Found</h1>")

    def _get_cookie(self, name: str) -> str:
        raw = self.headers.get("Cookie", "")
        for part in raw.split(";"):
            k, _, v = part.strip().partition("=")
            if k.strip() == name:
                return v.strip()
        return ""

    def _auth_enabled(self) -> bool:
        db = self.database
        return bool(db and db.config.servers.control_server.auth.enabled)

    def _is_authenticated(self) -> bool:
        if not self._auth_enabled():
            return True
        return _validate_session(self._get_cookie("mkdb_token"))

    def _sdk_username(self) -> str | None:
        """Return the control_user username if a valid SDK Bearer token is present."""
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return _validate_sdk_token(auth[7:].strip())
        return None

    def _caller_role(self) -> str:
        """Return the effective role of the caller (admin session → 'admin'; SDK token → user role)."""
        if self._is_authenticated():   # admin session or no auth required
            return "admin"
        sdk_user = self._sdk_username()
        if sdk_user and self.database:
            cu = self.database.config.control_users.get(sdk_user)
            if cu:
                return cu.role
        return ""   # unauthenticated

    # GET ─────────────────────────────────────────────────────────────────────

    def do_GET(self) -> None:
        path = urlparse(self.path).path.rstrip("/") or "/"

        if path == "/check_active":
            self._json(200, {"active": True})
            return

        if path == "/":
            self._redirect("/home")
            return

        # Always accessible — no auth check
        if path == "/auth/status":
            self._handle_auth_status()
            return
        if path == "/login":
            self._serve_file("login")
            return

        # All other pages require valid session when auth is enabled
        if not self._is_authenticated():
            self._redirect("/login")
            return

        self._serve_file(path.lstrip("/"))

    # POST ────────────────────────────────────────────────────────────────────

    def do_POST(self) -> None:
        path = urlparse(self.path).path

        if path == "/auth/login":
            self._handle_auth_login()
            return
        if path == "/auth/logout":
            self._handle_auth_logout()
            return
        if path == "/control/auth":
            self._handle_control_auth()
            return
        if path == "/control/auth/logout":
            self._handle_control_auth_logout()
            return

        # Admin session OR valid SDK Bearer token may call /control
        caller_role = self._caller_role()
        if not caller_role:
            self._json(401, {"status": "error", "message": "Authentication required"})
            return

        if path == "/control":
            self._handle_control(caller_role)
            return

        self._json(404, {"status": "error", "message": "Not found"})

    # Auth endpoints ──────────────────────────────────────────────────────────

    def _handle_auth_status(self) -> None:
        enabled = self._auth_enabled()
        authenticated = self._is_authenticated()
        has_password = False
        if self.database and enabled:
            has_password = bool(
                self.database.config.servers.control_server.auth.password_hash
            )
        self._json(200, {
            "enabled":       enabled,
            "authenticated": authenticated,
            "has_password":  has_password,
            "sessions":      active_session_count(),
        })

    def _handle_auth_login(self) -> None:
        try:
            data = self._read_body()
        except Exception as e:
            self._json(400, {"status": "error", "message": str(e)})
            return

        auth_cfg = (
            self.database.config.servers.control_server.auth
            if self.database else None
        )

        if not self._auth_enabled() or auth_cfg is None:
            # Auth disabled — hand out a short-lived session so the UI cookie
            # pattern works uniformly without requiring a real password check.
            token = _new_session(3600)
            self._json(200, {"status": "ok", "token": token})
            return

        password = data.get("password", "")

        if not auth_cfg.password_hash:
            # First-time setup: accept this password and store it.
            if not password:
                self._json(400, {"status": "error", "message": "Password cannot be empty"})
                return
            auth_cfg.password_hash = hash_password(password)
            self.database.config.save() # type: ignore
        elif not verify_password(password, auth_cfg.password_hash):
            self._json(401, {"status": "error", "message": "Incorrect password"})
            return

        token = _new_session(auth_cfg.session_ttl)
        self._json(200, {"status": "ok", "token": token})

    def _handle_auth_logout(self) -> None:
        token = self._get_cookie("mkdb_token")
        if token and token in _SESSIONS:
            del _SESSIONS[token]
        self._json(200, {"status": "ok"})

    # Control endpoint ────────────────────────────────────────────────────────

    def _handle_control_auth(self) -> None:
        """Authenticate a control/SDK user and return a Bearer token."""
        try:
            data = self._read_body()
        except Exception as e:
            self._json(400, {"status": "error", "message": str(e)})
            return
        username = str(data.get("username", "")).strip()
        password = str(data.get("password", ""))
        if not username or not password:
            self._json(400, {"status": "error", "message": "username and password required"})
            return
        db = self.database
        if db is None:
            self._json(503, {"status": "error", "message": "Database not available"})
            return
        cu = db.config.control_users.get(username)
        if cu is None or not verify_password(password, cu.password_hash):
            self._json(403, {"status": "error", "message": "Invalid credentials"})
            return
        token = _new_sdk_token(username)
        self._json(200, {"status": "ok", "token": token, "role": cu.role})

    def _handle_control_auth_logout(self) -> None:
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            _SDK_SESSIONS.pop(auth[7:].strip(), None)
        self._json(200, {"status": "ok"})

    def _handle_control(self, caller_role: str = "admin") -> None:
        try:
            data = self._read_body(max_bytes=10_485_760)
        except Exception as e:
            self._json(400, {"status": "error", "message": str(e)})
            return

        action = data.get("action")
        if action not in CONTROL_FUNCTIONS:
            self._json(400, {"status": "error", "message": f"Unknown action: {action!r}"})
            return

        # RBAC check
        required_role = _ACTION_ROLES.get(action, "admin")
        if _ROLE_ORDER.get(caller_role, -1) < _ROLE_ORDER.get(required_role, 2):
            self._json(403, {"status": "error",
                             "message": f"Action '{action}' requires role '{required_role}' (you have '{caller_role}')"})
            return

        try:
            result = CONTROL_FUNCTIONS[action](self.database, data)
            self._json(200, {"status": "success", "result": result})
        except Exception as e:
            from traceback import print_exc
            print_exc()
            self._json(500, {"status": "error", "message": str(e)})

    def log_message(self, format: str, *args) -> None:
        pass   # suppress default stdout logging


CONTROL_FUNCTIONS: dict = {}


def start_control_server(host: str, port: int, database=None):
    HTTPRequestHandler.database = database

    import inspect
    from mkdb.server.control.api import actions
    for name, obj in inspect.getmembers(actions):
        if inspect.isfunction(obj) and "api" in obj.__name__:
            CONTROL_FUNCTIONS[name] = obj

    srv = HTTPServer(
        name="Control-HTTP",
        host=host,
        port=port,
        responder=HTTPRequestHandler,  # type: ignore
    )
    return srv