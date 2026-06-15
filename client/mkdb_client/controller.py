import json
import urllib.request
import urllib.error
from typing import Optional, Any
from .exceptions import MkDBError

class ControllerError(MkDBError):
    """Raised when the control API returns an error response."""
    pass

class SubController:
    def __init__(self, parent: 'MkDBController'):
        self._parent = parent

    def _action(self, action: str, data: Optional[dict] = None) -> dict:
        return self._parent._action(action, data)

class DataUsersController(SubController):
    def list(self) -> list:
        return self._action("api_list_users")
    def create(self, username, password):
        return self._action("api_create_user", {"username": username, "password": password})
    def delete(self, username):
        return self._action("api_delete_user", {"username": username})
    def set_password(self, username, password):
        return self._action("api_set_user_password", {"username": username, "password": password})
    def set_store_access(self, username, store, read=True, write=False):
        return self._action("api_set_user_store_access", {"username": username, "store": store, "read": read, "write": write})
    def remove_store_access(self, username, store):
        return self._action("api_remove_user_store_access", {"username": username, "store": store})

class ControlUsersController(SubController):
    def list(self) -> list:
        return self._action("api_list_control_users")
    def create(self, username, password, role="viewer"):
        return self._action("api_create_control_user", {"username": username, "password": password, "role": role})
    def delete(self, username):
        return self._action("api_delete_control_user", {"username": username})
    def set_role(self, username, role):
        return self._action("api_set_control_user_role", {"username": username, "role": role})
    def set_password(self, username, password):
        return self._action("api_set_control_user_password", {"username": username, "password": password})

class SecurityController(SubController):
    def __init__(self, parent: 'MkDBController'):
        super().__init__(parent)
        self.users = DataUsersController(parent)
        self.rbac = ControlUsersController(parent)
    def get_settings(self): return self._action("api_get_data_security")
    def update_settings(self, protect_reads=None, disable_default_password=None):
        data = {}
        if protect_reads is not None: data["protect_reads"] = protect_reads
        if disable_default_password is not None: data["disable_default_password"] = disable_default_password
        return self._action("api_set_data_security", data)
    def get_auth_status(self): return self._action("api_get_auth_status")
    def enable_auth(self): return self._action("api_enable_auth")
    def disable_auth(self): return self._action("api_disable_auth")

class StoresController(SubController):
    def list(self) -> list: return self._action("api_list_stores")["stores"]
    def create(self, name, description=""): return self._action("api_create_store", {"name": name, "description": description})
    def delete(self, name): return self._action("api_delete_store", {"name": name})
    def get_config(self, name): return self._action("api_get_store_config", {"name": name})
    def update_config(self, name, **kwargs): return self._action("api_update_store_config", {"name": name, **kwargs})
    def get_metrics(self, name): return self._action("api_get_store_metrics", {"name": name})
    def reset_metrics(self, name): return self._action("api_reset_store_metrics", {"name": name})
    def restart_workers(self, name): return self._action("api_restart_workers", {"name": name})

class SystemController(SubController):
    def get_status(self): return self._action("api_get_server_status")
    def control_server(self, server, op): return self._action("api_server_control", {"server": server, "op": op})
    def get_settings(self): return self._action("api_get_db_settings")
    def update_settings(self, **kwargs): return self._action("api_update_db_settings", kwargs)
    def get_event_log(self, limit=50, level=""): 
        data = {"limit": limit}
        if level: data["level"] = level
        return self._action("api_get_event_log", data)["events"]
    def get_rate_limit_log(self, limit=200): return self._action("api_get_rate_limit_log", {"limit": limit})["events"]

class MkDBController:
    def __init__(self, host="127.0.0.1", port=8090, timeout=10.0):
        self._base = f"http://{host}:{port}"
        self._timeout = timeout
        self._token = None
        self.role = None
        self.stores = StoresController(self)
        self.security = SecurityController(self)
        self.system = SystemController(self)

    def login(self, username, password):
        resp = self._post_raw("/control/auth", {"username": username, "password": password}, auth=False)
        self._token = resp["token"]
        self.role = resp.get("role", "viewer")
        return self.role

    def logout(self):
        if self._token:
            try: self._post_raw("/auth/logout", {})
            except: pass
            self._token = None; self.role = None

    def dashboard(self): return self._action("api_get_dashboard")
    def db_info(self): return self._action("api_get_db_info")

    def _action(self, action, data=None):
        payload = {"action": action}
        if data: payload.update(data)
        resp = self._post_raw("/control", payload)
        return resp.get("result", resp)

    def _post_raw(self, path, payload, auth=True):
        url = self._base + path
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if auth and self._token: headers["Authorization"] = f"Bearer {self._token}"
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=self._timeout) as r:
            return json.loads(r.read().decode("utf-8"))
