import os
import time
from typing import Union

from colorama import Fore
from mkdb.config.db import mkdb_config
from mkdb.server.coms.http import HTTPServer


class mkdb:
    def __init__(self, config:Union[dict, mkdb_config]):
        from mkdb.db.objects.store import store

        if type(config) == dict:
            config = mkdb_config(config)
        self.config:mkdb_config = config #type: ignore
        self.stores:dict[str, "store"] = {}
        self._servers: dict = {}   # "http" | "socket" | "control" -> instance
        self._started_at: float = 0.0  # unix timestamp of when run() was called
        
        self.setup()

    @property
    def file_path(self):
        return self.config.base_path

    def __verify__(self, config:Union[mkdb_config, None]=None):
        """
        Verify the database configuration and setup.
        """
        if config is None:
            config = self.config

        if config.name in [None, ""]:
            raise ValueError("Database name cannot be empty.")
        if config.base_path in [None, ""]:
            raise ValueError("Base path cannot be empty.")
        if config.servers.socket_server.enabled:
            if config.servers.socket_server.address.host in [None, ""]:
                raise ValueError("Socket server enabled but no host specified.")
            if config.servers.socket_server.address.port == 0:
                raise ValueError("Socket server enabled but no port specified.")
        if config.servers.http_server.enabled:
            if config.servers.http_server.address.host in [None, ""]:
                raise ValueError("HTTP server enabled but no host specified.")
            if config.servers.http_server.address.port == 0:
                raise ValueError("HTTP server enabled but no port specified.")
    
    def setup(self):
        try:
            self.__verify__()
        except Exception as e:
            print(f"Error verifying database configuration: {Fore.RED}{e}{Fore.RESET}")
            raise e
            return
        
        os.makedirs(os.path.join(self.file_path, "stores"), exist_ok=True)
        for store_name, store_config in self.config.stores.items():
            from mkdb.db.objects.store import store
            if store_name not in self.stores:
                s = store(self, store_config)
                self.stores[store_name] = s
            else:
                s = self.stores[store_name]
                s.config.update(store_config.json)
            s.setup()

    def run(self):
        print(f"{Fore.GREEN}Database '{self.config.name}' initialized at {os.getcwd()}{Fore.RESET}")

        # Pre-flight check: ensure control-server actions are mapped to RBAC roles
        from mkdb.server.control import server as ctrl_srv
        from mkdb.server.control.api import actions as ctrl_actions
        import inspect

        for name, _ in inspect.getmembers(ctrl_actions, inspect.isfunction):
            if name.startswith("api_") and name not in ctrl_srv._ACTION_ROLES:
                # Default to admin for anything not explicitly specified
                ctrl_srv._ACTION_ROLES[name] = "admin"

        print("Starting servers...")
        self._started_at = time.time()
        if self.config.servers.http_server.enabled:
            from mkdb.server.coms.http import HTTPServer
            from mkdb.server.coms.http_handlers import HTTPDataHandler
            HTTPDataHandler.database = self
            self._servers["http"] = HTTPServer(
                name="Communication-HTTP",
                host=self.config.servers.http_server.address.host,
                port=self.config.servers.http_server.address.port,
                responder=HTTPDataHandler,
            )

        if self.config.servers.socket_server.enabled:
            from mkdb.server.coms.socket import SocketServer
            _sock_srv = SocketServer(
                host=self.config.servers.socket_server.address.host,
                port=self.config.servers.socket_server.address.port,
                database=self,
                heartbeat_interval=self.config.servers.socket_server.heartbeat_interval,
                max_clients=self.config.servers.socket_server.max_clients,
                recv_timeout=self.config.servers.socket_server.recv_timeout,
            )
            _sock_srv.start()
            self._servers["socket"] = _sock_srv

        if self.config.servers.control_server.enabled:
            from mkdb.server.control.server import start_control_server
            self._servers["control"] = start_control_server(
                host=self.config.servers.control_server.address.host,
                port=self.config.servers.control_server.address.port,
                database=self
            )
            
        print(f"MkDB {Fore.CYAN}'{self.config.name}'{Fore.RESET} is running at {Fore.GREEN}{os.getcwd()}{Fore.RESET}")

        while True:
            try:
                time.sleep(1)
                pass
            except KeyboardInterrupt:
                print(f"{Fore.YELLOW}Shutting down MkDB '{self.config.name}'...{Fore.RESET}")
                break
        self.shutdown()

    def get_server_status(self) -> dict:
        """Return running/stopped status for all three servers."""
        return {name: (self._servers.get(name) is not None) for name in ("http", "socket", "control")}

    def get_uptime(self) -> float:
        """Return seconds since run() was called, or 0 if not yet started."""
        return (time.time() - self._started_at) if self._started_at else 0.0

    def stop_server(self, name: str) -> None:
        """Stop a named server and clear its reference."""
        srv = self._servers.get(name)
        if srv is None:
            return
        if hasattr(srv, "stop"):
            srv.stop()
        elif hasattr(srv, "shutdown"):
            try:
                srv.shutdown()
            except Exception:
                pass
        self._servers[name] = None

    def start_server(self, name: str) -> None:
        """Start a named server using current config. No-op if already running."""
        if self._servers.get(name) is not None:
            return
        cfg = self.config.servers
        if name == "http":
            if not cfg.http_server.enabled:
                return
            from mkdb.server.coms.http import HTTPServer
            from mkdb.server.coms.http_handlers import HTTPDataHandler
            HTTPDataHandler.database = self
            self._servers["http"] = HTTPServer(
                name="Communication-HTTP",
                host=cfg.http_server.address.host,
                port=cfg.http_server.address.port,
                responder=HTTPDataHandler,
            )
        elif name == "socket":
            if not cfg.socket_server.enabled:
                return
            from mkdb.server.coms.socket import SocketServer
            srv = SocketServer(
                host=cfg.socket_server.address.host,
                port=cfg.socket_server.address.port,
                database=self,
                heartbeat_interval=cfg.socket_server.heartbeat_interval,
                max_clients=cfg.socket_server.max_clients,
                recv_timeout=cfg.socket_server.recv_timeout,
            )
            srv.start()
            self._servers["socket"] = srv
        elif name == "control":
            if not cfg.control_server.enabled:
                return
            from mkdb.server.control.server import start_control_server
            self._servers["control"] = start_control_server(
                host=cfg.control_server.address.host,
                port=cfg.control_server.address.port,
                database=self,
            )

    def restart_server(self, name: str) -> None:
        """Stop then start a named server."""
        self.stop_server(name)
        self.start_server(name)

    def shutdown(self) -> None:
        """Stop all servers and tear down all stores."""
        for name in ("http", "socket", "control"):
            self.stop_server(name)
        for s in self.stores.values():
            try:
                s.teardown()
            except Exception:
                pass
        self.stores.clear()
        print(f"{Fore.YELLOW}MkDB '{self.config.name}' shut down.{Fore.RESET}")

    def update_from_config(self, new_config:Union[dict, mkdb_config]):
        if type(new_config) == dict:
            new_config = mkdb_config(new_config)
        try:
            self.__verify__(new_config) #type: ignore
            self.config = new_config #type: ignore
            print(f"{Fore.GREEN}Configuration updated successfully.{Fore.RESET}")
        except Exception as e:
            print(f"Error updating database configuration: {Fore.RED}{e}{Fore.RESET}")
            return
        
        self.setup()