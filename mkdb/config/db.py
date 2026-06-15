import os

from mkdb.filing import write_json
from mkdb.config.server import control_server, socket_server, http_server
from mkdb.objects import base_object

class query_worker_config(base_object):
    def __init__(self, data:dict={}):
        self.parallel_enabled = False   # set True to spin up worker processes
        self.worker_count = 0           # 0 = auto (os.cpu_count())
        self.worker_cache_size = 500    # max records in each worker's local cache
        self.worker_cache_ttl = 30      # seconds before a worker cache entry expires
        self.task_timeout = 30.0        # seconds before submit() raises TimeoutError
        self.reboot_interval_hours = 0  # 0 = disabled, else reboot workers every X hours
        super().__init__(data)

class ram_config(base_object):
    def __init__(self, data:dict={}):
        self.max_size = 1000
        self.ttl = 3600
        self.cleanup_interval = 600
        self.clean_type = "lru"
        self.update_on_access = True
        self.index = True
        self.index_cache_ttl = 3600
        self.query_fields = True
        self.query_fields_cache_ttl = 3600
        # 0 = keep all query indexes in RAM.
        # >0 = if an index file on disk exceeds this many bytes, don't load
        #      it into RAM; query and write directly from/to the file instead.
        self.index_ram_threshold_bytes = 0
        super().__init__(data)

TOKEN_SOFT_LIMIT = 28   # health = "low"  when auto_expand is on and length >= this
TOKEN_HARD_LIMIT = 32   # health = "degraded" regardless; performance warning threshold

class entity_config(base_object):
    def __init__(self, data:dict={}):
        self.token_chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        self.token_length = 16
        self.auto_expand = False
        super().__init__(data)

    @property
    def health(self) -> dict:
        """Return entity-config health status and an explanatory message."""
        if self.token_length > TOKEN_HARD_LIMIT:
            return {
                "status": "degraded",
                "message": (
                    f"Token length {self.token_length} exceeds the recommended "
                    f"maximum of {TOKEN_HARD_LIMIT} characters. "
                    "Performance may degrade — consider reducing it."
                ),
            }
        if self.auto_expand and self.token_length >= TOKEN_SOFT_LIMIT:
            return {
                "status": "low",
                "message": (
                    f"Token length {self.token_length} is approaching the "
                    f"{TOKEN_HARD_LIMIT}-character performance limit. "
                    "With auto expand enabled this may increase further, "
                    "degrading database performance."
                ),
            }
        return {"status": "ok", "message": ""}

class file_config(base_object):
    def __init__(self, data:dict={}):
        self.file_size_split_trigger = 20000000
        self.type = "rolling_log"
        self.blob_threshold        = 5 * 1024 * 1024   # 5 MB
        self.segment_threshold     = 50 * 1024 * 1024  # 50 MB
        self.parity_nsym           = 10
        self.compaction_dead_ratio = 0.3
        self.compaction_interval   = 60
        super().__init__(data)

class field_schema(base_object):
    def __init__(self, data={}):
        self.name        = ""
        self.queryable   = False    # False | "exact" | "full-text" | "numeric"
        self.indexed     = False
        super().__init__(data)

class schema_config(base_object):
    def __init__(self, data={}):
        self.fields: dict = {}
        super().__init__(data)
        self._normalise_fields()

    def _normalise_fields(self):
        self.fields = {
            k: field_schema(v) if isinstance(v, dict) else v
            for k, v in self.fields.items()
        }

    def update(self, data: dict):
        super().update(data)
        self._normalise_fields()

class write_queue_config(base_object):
    def __init__(self, data={}):
        self.debounce_window = 5.0
        self.max_pending     = 10000
        super().__init__(data)

class store_rate_limit(base_object):
    """Per-store IP rate limit for the HTTP data plane."""
    def __init__(self, data: dict = {}):
        self.enabled = False
        self.max_requests_per_second = 100
        super().__init__(data)

class store_config(base_object):
    def __init__(self, data:dict={}):
        self.name = ""
        self.description = ""
        self.default_query_limit = 300      # 0 = unlimited; default limit if query doesn't provide one
        self.protect_reads = False          # when True, read/query also require auth
        self.slow_query_threshold_ms = 0.0  # 0 = disabled; >0 = log queries slower than X ms
        self.max_query_execution_time_ms = 0.0  # 0 = disabled; >0 = hard limit to stop long queries
        self.query_recursion_limit = 12     # max depth of nested $and/$or blocks
        self.client_id_header = ""          # HTTP header to use as client identifier; "" = remote_addr
        self.schema_auto_discover_on_boot  = False  # scan stored records for new fields on startup
        self.schema_auto_discover_on_write = False  # add unseen fields to schema on every write
        self.nested_queries_enabled = False         # allow dot-notation lookups (e.g. "a.b.c"); may impact performance
        self.file_config = file_config(data.get("file_config", {}))
        self.entity_config = entity_config(data.get("entity_config", {}))
        self.ram_config = ram_config(data.get("ram_config", {}))
        self.query_worker_config = query_worker_config(data.get("query_worker_config", {}))
        self.schema_config      = schema_config(data.get("schema_config", {}))
        self.write_queue_config = write_queue_config(data.get("write_queue_config", {}))
        self.rate_limit         = store_rate_limit(data.get("rate_limit", {}))
        super().__init__(data)

    @property
    def query_recursion_limit_health(self) -> dict:
        """Warning for high recursion limits."""
        if self.query_recursion_limit > 20:
            return {
                "status": "warning",
                "message": (
                    f"Recursion limit {self.query_recursion_limit} is unrecommended. "
                    "Deeply nested queries can cause high CPU usage and potential stack issues. "
                    "Consider flattening your query structure."
                )
            }
        return {"status": "ok", "message": ""}

class backup_config(base_object):
    def __init__(self, data:dict={}):
        self.enabled = False
        self.interval = 3600
        self.directory = "backups"
        super().__init__(data)

class store_permission(base_object):
    """Read/write permissions for a user on a specific store."""
    def __init__(self, data: dict = {}):
        self.read  = True
        self.write = False
        super().__init__(data)

class db_user(base_object):
    """A database user who can authenticate against the data plane."""
    def __init__(self, data: dict = {}):
        self.username      = ""
        self.password_hash = ""
        self.stores: dict  = {}   # store_name -> store_permission
        super().__init__(data)
        self.stores = {
            k: store_permission(v) if isinstance(v, dict) else v
            for k, v in self.stores.items()
        }

class data_security_config(base_object):
    """Security settings for the data plane (HTTP + Socket)."""
    def __init__(self, data: dict = {}):
        # When True, GET/read/query requests also require authentication.
        # When False (default), only write/delete requests require auth.
        self.protect_reads = False
        # When True, the default 'mk_db' write password is disabled.
        # Writes will be rejected unless real users are configured.
        self.disable_default_password = False
        super().__init__(data)

# ── Control-plane RBAC ───────────────────────────────────────────────────────
# Roles (least → most privileged):
#   viewer   – read-only: list stores, get configs, view logs
#   operator – viewer + create/delete stores, update store configs
#   admin    – operator + all control settings (auth, users, server config)

CONTROL_ROLES = ("viewer", "operator", "admin")

class control_user(base_object):
    """A control-plane SDK user with a role."""
    def __init__(self, data: dict = {}):
        self.username      = ""
        self.password_hash = ""
        self.role          = "viewer"   # "viewer" | "operator" | "admin"
        super().__init__(data)

class mkdb_servers_config(base_object):
    def __init__(self, data:dict={}):
        self.control_server = control_server(data.get("control_server", {}))
        self.socket_server = socket_server(data.get("socket_server", {}))
        self.http_server = http_server(data.get("http_server", {}))
        super().__init__(data)

class mkdb_config(base_object):
    def __init__(self, data:dict={}):
        self.name = ""
        self.storage_nodes: list = []
        self.stores:dict[str, store_config] = {}
        super().__init__(data)
        self.servers = mkdb_servers_config(data.get("servers", {}))
        self.backup = backup_config(data.get("backup", {}))
        for store_name, store_data in data.get("stores", {}).items():
            self.stores[store_name] = store_config(store_data)
        self.users: dict = {}   # username -> db_user
        for uname, udata in data.get("users", {}).items():
            self.users[uname] = db_user(udata)
        self.data_security = data_security_config(data.get("data_security", {}))
        self.control_users: dict = {}   # username -> control_user
        for uname, udata in data.get("control_users", {}).items():
            self.control_users[uname] = control_user(udata)

    @property
    def base_path(self):
        if not hasattr(self, "__base_path__"):
            self.__base_path__ = os.getcwd()
        return self.__base_path__

    def new_store(self, store_name:str, description:str=""):
        """Add a new store to the configuration."""
        if store_name in self.stores:
            raise ValueError(f"Store '{store_name}' already exists in the configuration.")
        
        new_store_config = store_config()
        new_store_config.name = store_name
        new_store_config.description = description
        self.stores[store_name] = new_store_config
        print(f"Store '{store_name}' added to configuration.")
        self.save()

    def save(self):
        """Save the current configuration to a file."""
        config_path = os.path.join(self.base_path, "config.json")
        write_json(config_path, self.json)
        print(f"Configuration saved to {config_path}")