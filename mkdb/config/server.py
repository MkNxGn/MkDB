from mkdb.objects import base_object

class server_address_config(base_object):
    def __init__(self, data:dict={}):
        self.host = ""
        self.port = 0
        super().__init__(data)


class auth_config(base_object):
    """Authentication configuration shared by all server types."""
    def __init__(self, data:dict={}):
        self.enabled = False
        self.password_hash = ""   # empty = no password set; format: pbkdf2:sha256:{iters}:{salt}:{hash}
        self.session_ttl = 3600   # seconds until a session token expires
        super().__init__(data)


class control_server(base_object):
    def __init__(self, data:dict={}):
        self.enabled = True
        self.address = server_address_config(data.get("address", {}))
        self.auth = auth_config(data.get("auth", {}))
        super().__init__(data)
        if self.enabled and self.address.host in [None, ""]:
            print("Control server enabled but no host specified. Defaulting to localhost.")
            self.address.host = "localhost"
        if self.enabled and self.address.port == 0:
            print("Control server enabled but no port specified. Defaulting to port 80.")
            self.address.port = 80


class socket_server(base_object):
    def __init__(self, data:dict={}):
        self.enabled = True
        self.address = server_address_config(data.get("address", {}))
        self.auth = auth_config(data.get("auth", {}))
        self.heartbeat_interval = 5.0
        self.max_clients = 100
        self.recv_timeout = 30.0
        super().__init__(data)

class http_server(base_object):
    def __init__(self, data:dict={}):
        self.enabled = False
        self.address = server_address_config(data.get("address", {}))
        self.auth = auth_config(data.get("auth", {}))
        self.max_body_size = 10 * 1024 * 1024
        self.cors_enabled = False
        self.cors_origins: list = ["*"]
        self.max_requests_per_second = 100
        super().__init__(data)