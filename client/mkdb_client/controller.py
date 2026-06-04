"""
MkDB Controller — programmatic access to the MkDB control-plane HTTP API.

Usage:
    from mkdb_client import MkDBController

    ctrl = MkDBController(host="127.0.0.1", port=8090)
    ctrl.login(username="admin", password="secret")

    print(ctrl.dashboard())
    print(ctrl.list_stores())

    ctrl.create_store("orders", description="Customer orders")
    ctrl.logout()
"""

import json
import urllib.request
import urllib.error
from typing import Optional


class ControllerError(Exception):
    """Raised when the control API returns an error response."""
    pass


class MkDBController:
    def __init__(self, host: str = "127.0.0.1", port: int = 8090,
                 timeout: float = 10.0):
        """
        Parameters
        ----------
        host    : control server host
        port    : control server port (default 8090, matches MkDB default)
        timeout : HTTP request timeout in seconds
        """
        self._base = f"http://{host}:{port}"
        self._timeout = timeout
        self._token: Optional[str] = None
        self.role: Optional[str] = None

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def login(self, username: str, password: str) -> str:
        """
        Authenticate as a named control user (RBAC) and store the session token.
        Returns the user's role: 'viewer' | 'operator' | 'admin'.

        Control users are managed via the control panel or:
            ctrl.create_control_user("alice", "secret", role="operator")
        """
        resp = self._post_raw("/control/auth",
                              {"username": username, "password": password},
                              auth=False)
        self._token = resp["token"]
        self.role = resp.get("role", "viewer")
        return self.role

    def logout(self) -> None:
        """Invalidate the current session token."""
        if self._token:
            try:
                # Works for both auth paths
                self._post_raw("/auth/logout", {})
            except Exception:
                pass
            self._token = None
            self.role = None

    # ------------------------------------------------------------------
    # Dashboard / info
    # ------------------------------------------------------------------

    def dashboard(self) -> dict:
        """Full dashboard snapshot: DB info, server status, per-store metrics, events."""
        return self._action("api_get_dashboard")

    def db_info(self) -> dict:
        """Basic database metadata."""
        return self._action("api_get_db_info")

    def event_log(self, limit: int = 50, level: str = "") -> list:
        """Recent system events. level filters to 'info'|'warning'|'error'|'recovery'."""
        data: dict = {"limit": limit}
        if level:
            data["level"] = level
        return self._action("api_get_event_log", data)["events"]

    # ------------------------------------------------------------------
    # Stores
    # ------------------------------------------------------------------

    def list_stores(self) -> list:
        """List all stores."""
        return self._action("api_list_stores")["stores"]

    def create_store(self, name: str, description: str = "") -> dict:
        """Create a new store."""
        return self._action("api_create_store", {"name": name, "description": description})

    def delete_store(self, name: str) -> dict:
        """Delete a store."""
        return self._action("api_delete_store", {"name": name})

    def get_store_config(self, name: str) -> dict:
        """Get full configuration for a store."""
        return self._action("api_get_store_config", {"name": name})

    def update_store_config(self, name: str, **kwargs) -> dict:
        """Update store configuration. Pass any subset of config fields as kwargs."""
        return self._action("api_update_store_config", {"name": name, **kwargs})

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def store_metrics(self, name: str) -> dict:
        """Live request, bandwidth, RAM-cache and client metrics for a store."""
        return self._action("api_get_store_metrics", {"name": name})

    def all_store_metrics(self) -> list:
        """Metrics for every store."""
        return self._action("api_get_all_store_metrics")

    def reset_store_metrics(self, name: str) -> dict:
        """Clear in-memory metrics for a store."""
        return self._action("api_reset_store_metrics", {"name": name})

    # ------------------------------------------------------------------
    # Database settings
    # ------------------------------------------------------------------

    def db_settings(self) -> dict:
        """Return editable database and server settings."""
        return self._action("api_get_db_settings")

    def update_db_settings(self, **kwargs) -> dict:
        """Update top-level database settings (name, storage_nodes)."""
        return self._action("api_update_db_settings", kwargs)

    # ------------------------------------------------------------------
    # Server control
    # ------------------------------------------------------------------

    def server_status(self) -> dict:
        """Running/stopped status for all three servers (socket, http, control)."""
        return self._action("api_get_server_status")

    def server_start(self, server: str) -> dict:
        """Start a server. server = 'socket' | 'http' | 'control'."""
        return self._action("api_server_control", {"server": server, "op": "start"})

    def server_stop(self, server: str) -> dict:
        """Stop a server. server = 'socket' | 'http' | 'control'."""
        return self._action("api_server_control", {"server": server, "op": "stop"})

    def server_restart(self, server: str) -> dict:
        """Restart a server. server = 'socket' | 'http' | 'control'."""
        return self._action("api_server_control", {"server": server, "op": "restart"})

    def update_server_config(self, server: str, **kwargs) -> dict:
        """Persist config changes for a server (host, port, enabled, etc.)."""
        return self._action("api_update_server_config", {"server": server, **kwargs})

    # ------------------------------------------------------------------
    # Data-plane users
    # ------------------------------------------------------------------

    def list_users(self) -> list:
        """List all data-plane users."""
        return self._action("api_list_users")

    def create_user(self, username: str, password: str) -> dict:
        """Create a data-plane user."""
        return self._action("api_create_user", {"username": username, "password": password})

    def delete_user(self, username: str) -> dict:
        """Delete a data-plane user."""
        return self._action("api_delete_user", {"username": username})

    def set_user_password(self, username: str, password: str) -> dict:
        """Change a data-plane user's password."""
        return self._action("api_set_user_password", {"username": username, "password": password})

    def set_user_store_access(self, username: str, store: str,
                               read: bool = True, write: bool = False) -> dict:
        """Grant or update a user's access to a store."""
        return self._action("api_set_user_store_access",
                            {"username": username, "store": store, "read": read, "write": write})

    def remove_user_store_access(self, username: str, store: str) -> dict:
        """Remove a user's access to a store."""
        return self._action("api_remove_user_store_access",
                            {"username": username, "store": store})

    # ------------------------------------------------------------------
    # Control-plane users (RBAC)
    # ------------------------------------------------------------------

    def list_control_users(self) -> list:
        """List all control/SDK users and their roles."""
        return self._action("api_list_control_users")

    def create_control_user(self, username: str, password: str,
                            role: str = "viewer") -> dict:
        """Create a control user. role = 'viewer' | 'operator' | 'admin'."""
        return self._action("api_create_control_user",
                            {"username": username, "password": password, "role": role})

    def delete_control_user(self, username: str) -> dict:
        """Delete a control user."""
        return self._action("api_delete_control_user", {"username": username})

    def set_control_user_role(self, username: str, role: str) -> dict:
        """Change a control user's role."""
        return self._action("api_set_control_user_role", {"username": username, "role": role})

    def set_control_user_password(self, username: str, password: str) -> dict:
        """Change a control user's password."""
        return self._action("api_set_control_user_password",
                            {"username": username, "password": password})

    # ------------------------------------------------------------------
    # Auth settings
    # ------------------------------------------------------------------

    def auth_status(self) -> dict:
        """Return current control-server auth configuration."""
        return self._action("api_get_auth_status")

    def set_auth_password(self, password: str) -> dict:
        """Set the control-server password and enable auth."""
        return self._action("api_set_auth_password", {"password": password})

    def enable_auth(self) -> dict:
        """Enable authentication on the control server."""
        return self._action("api_enable_auth")

    def disable_auth(self) -> dict:
        """Disable authentication on the control server."""
        return self._action("api_disable_auth")

    # ------------------------------------------------------------------
    # Data security
    # ------------------------------------------------------------------

    def data_security(self) -> dict:
        """Return data-plane security settings."""
        return self._action("api_get_data_security")

    def set_data_security(self, protect_reads: bool = None,
                          disable_default_password: bool = None) -> dict:
        """
        Update data-plane security settings.

        protect_reads              : require auth for reads too (default False)
        disable_default_password   : block the built-in 'mk_db' write password
                                     so only configured users can write (default False)
        """
        data: dict = {}
        if protect_reads is not None:
            data["protect_reads"] = protect_reads
        if disable_default_password is not None:
            data["disable_default_password"] = disable_default_password
        return self._action("api_set_data_security", data)

    # ------------------------------------------------------------------
    # Rate-limit log
    # ------------------------------------------------------------------

    def rate_limit_log(self, limit: int = 200) -> list:
        """Return recent rate-limit events."""
        return self._action("api_get_rate_limit_log", {"limit": limit})["events"]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _action(self, action: str, data: Optional[dict] = None) -> dict:
        """Send a control action and return the result dict."""
        payload = {"action": action}
        if data:
            payload.update(data)
        resp = self._post_raw("/control", payload)
        # /control wraps result under "result" key
        if "result" in resp:
            return resp["result"]
        return resp

    def _post_raw(self, path: str, payload: dict, auth: bool = True) -> dict:
        url = self._base + path
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if auth and self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body_bytes = b""
            try:
                body_bytes = exc.read()
            except Exception:
                pass
            try:
                err = json.loads(body_bytes.decode("utf-8"))
                raise ControllerError(err.get("message", str(exc))) from exc
            except (ValueError, KeyError):
                raise ControllerError(str(exc)) from exc
