# MkDB Python SDK Reference

The `pymkdb-client` package provides two clients:

- **`MkDBClient`** — data-plane client for reading, writing, querying, and subscribing to live updates.
- **`MkDBController`** — control-plane client for managing stores, users, server settings, and metrics via the HTTP control API.

## Installation

```bash
pip install pymkdb-client
```

---

## MkDBClient

### Initialisation

```python
from mkdb_client import MkDBClient

client = MkDBClient(
    host="127.0.0.1",
    port=9001,
    access="RW",        # "R", "W", or "RW"
    username="",        # required if the server has users configured
    password="mk_db",   # default write key; set "" for read-only
    recv_timeout=30.0,  # seconds to wait for a response
    transport="socket", # "socket" (default) or "http"
)
client.connect()
# ... use the client ...
client.close()
```

`transport="socket"` opens a persistent TCP connection and supports pub-sub (`on_update`). The socket protocol is **multiplexed**, allowing multiple concurrent requests and out-of-order responses.  
`transport="http"` makes a fresh HTTP request per call — no pub-sub support.

### Async Support (Multiplexing)

The socket-based `MkDBClient` supports asynchronous operations through the **Multiplexed Protocol**. This allows you to fire off multiple requests without waiting for previous ones to finish.

- **Non-blocking:** Every call returns a `MkDBTask` immediately.
- **Out-of-Order:** Small queries won't be blocked by large, slow ones on the same connection.
- **Fail-Fast:** Uses a "Receipt ACK" system. If the server doesn't acknowledge receipt within 1s, the client detects the dead connection and reconnects immediately.

```python
# Launch multiple tasks in parallel
task1 = client.query_async("products", {"price": {"lt": 10}})
task2 = client.get_async("stats", "today")

# Do other work...
print("Working...")

# Wait for results later
results = task1.result()  # blocks until ready
stats = task2.result()
```

---

### `client.get(store, record_id, as_type=None) → GetResponse`

Read a single record by ID.

```python
resp = client.get("products", "prod_001")
if resp.found:
    print(resp.data)   # dict (default) or SnapshotBaseObject (if tracking enabled)
```

- **`as_type`**: Optional class to wrap the data. If the class inherits from `SnapshotBaseObject`, it enables `.patch()`.
- **Note**: If `track_records=True` was passed to the client constructor, this returns a `SnapshotBaseObject` by default.

See [Record Tracking](RECORD_TRACKING.md) for details on using `.patch()`.

---

### `client.set(store, record_id, delta) → WriteResponse`

Write or update a record. Only the supplied fields are changed (delta write).

```python
resp = client.set("products", "prod_001", {"name": "Steel Bolt", "price": 9.99})
print(resp.record_id)
```

Nested objects are stored as-is. You can query them using dot-notation if the server is configured via `nested_queries_enabled: true`.

```python
client.set("products", "prod_001", {"meta": {"colour": "silver"}})
```

**Async version:** `client.set_async(store, record_id, delta) → MkDBTask`

---

### `client.insert(store, delta) → WriteResponse`

Write a new record with a **server-generated ID**.

```python
resp = client.insert("products", {"name": "Widget", "price": 4.99})
print(resp.record_id)  # e.g. "aB3xKq7mNpRt"
```

**Async version:** `client.insert_async(store, delta) → MkDBTask`

---

### `client.generate_id(store) → GenerateIdResponse`

Ask the server to reserve a unique ID without writing a record.

```python
resp = client.generate_id("products")
print(resp.record_id)
```

---

### `client.delete(store, record_id) → DeleteResponse`

Delete a record.

```python
resp = client.delete("products", "prod_001")
print(resp.record_id)
```

---

### `client.query(store, filter_dict, hydrate=False, sort=None, limit=None, offset=None) → QueryResponse`

Search for records in a store. You can pass a raw `dict` or a `Q` object.

```python
from mkdb_client import Q

# Fluent query builder with logical operators and pagination
q = (Q.field("price").lt(100) 
     & Q.field("category").is_included(["deals", "clearance"])) \
     .sort("-price").limit(10)

results = client.query("products", q, hydrate=True)

for record in results:
    print(record["_id"], record["name"])
```

**Filter syntax:**

| Filter | Meaning |
|---|---|
| `{"field": "exact"}` | Exact string match |
| `{"field": ["kw1", "kw2"]}` | Full-text AND search |
| `{"field": 42}` | Numeric exact match |
| `{"field": {"gt": 5, "lte": 20}}` | Numeric range (`gt`, `gte`, `lt`, `lte`) |
| `{"field": {"in": [1, 2]}}` | Inclusion check (`in`, `nin`) |
| `{"field": {"exists": True}}` | Field existence check |
| Multiple keys | AND — all conditions must match |

**Logical Operators (Q Builder):**
- `&` : Logical AND
- `|` : Logical OR

```python
q = Q.field("deleted").eq(False) & (Q.field("type").eq("A") | Q.field("type").eq("B"))
```

`QueryResponse` attributes: `count`, `ids`, `records` (only when `hydrate=True`), `store`.  
It is truthy when `count > 0` and supports `len()` and iteration.

**Async version:** `client.query_async(store, filter_dict, hydrate=False, sort=None, limit=None, offset=None) → MkDBTask`


---

### `client.on_update(store, callback)` *(socket transport only)*

Subscribe to live record changes for a store.

```python
def on_change(event):
    print(event["op"], event["record_id"])   # "write" or "delete"

client.on_update("products", on_change)
```

The callback receives a dict with at minimum: `store`, `record_id`, `op` (`"write"` | `"delete"`).

---

### `client.auth` — AuthManager

A simple built-in credential helper that stores hashed passwords inside MkDB itself (under the `__auth__` store).

```python
# Register a new user
client.auth.register("john_doe", "super_secret")

# Validate credentials — returns True/False
ok = client.auth.validate("john_doe", "super_secret")
```

Passwords are never stored in plaintext. The helper generates a random salt and stores `SHA-256(password + salt)`.

---

### Exceptions

All exceptions inherit from `MkDBError`.

```python
from mkdb_client import (
    MkDBError,             # base
    MkDBConnectionError,   # connect/disconnect failure (also ConnectionError)
    MkDBAuthError,         # bad credentials / access denied (also PermissionError)
    MkDBTimeoutError,      # no response within recv_timeout (also TimeoutError)
    MkDBTransportError,    # HTTP 4xx/5xx (HTTP transport only)
    MkDBServerError,       # server returned an error — base for the below
    MkDBStoreNotFoundError,  # store does not exist (also KeyError)
    MkDBRecordNotFoundError, # record ID not found (also KeyError)
    MkDBStoreExistsError,    # store already exists on create
    MkDBQueryError,          # invalid or malformed filter (also ValueError)
)
```

```python
try:
    resp = client.get("products", "prod_001")
except MkDBRecordNotFoundError:
    print("Record does not exist")
except MkDBStoreNotFoundError:
    print("Store does not exist")
except MkDBAuthError:
    print("Authentication failed")
except MkDBTimeoutError:
    print("Request timed out")
except MkDBConnectionError:
    print("Could not reach server")
```

---

## MkDBController

`MkDBController` manages the database through the control-plane HTTP API. It requires a running control server and a valid control user with an appropriate role (`viewer`, `operator`, or `admin`).

### Initialisation & auth

```python
from mkdb_client import MkDBController

ctrl = MkDBController(host="127.0.0.1", port=8090, timeout=10.0)
role = ctrl.login(username="admin", password="secret")  # returns role string
# ...
ctrl.logout()
```

### Stores

```python
ctrl.list_stores()                                  # → list of store dicts
ctrl.create_store("orders", description="...")      # → dict
ctrl.delete_store("old_store")                      # archives data to stores/__archived__/
ctrl.get_store_config("orders")                     # → full config dict
ctrl.update_store_config("orders", ram_config={"max_size": 5000})
```

### Metrics

```python
ctrl.all_store_metrics()          # → list of per-store metric dicts
ctrl.store_metrics("orders")      # → single store metrics dict
ctrl.reset_store_metrics("orders")
```

### Server control

```python
ctrl.server_status()              # → {"http": True, "socket": True, "control": True}
ctrl.server_stop("http")
ctrl.server_start("http")
ctrl.server_restart("socket")
ctrl.update_server_config("http", port=9002)
```

### Data-plane users

```python
ctrl.list_users()
ctrl.create_user("alice", "secret")
ctrl.set_user_store_access("alice", "orders", read=True, write=True)
ctrl.remove_user_store_access("alice", "orders")
ctrl.set_user_password("alice", "new_secret")
ctrl.delete_user("alice")
```

### Control-plane users (RBAC)

```python
ctrl.list_control_users()
ctrl.create_control_user("bob", "secret", role="operator")  # viewer | operator | admin
ctrl.set_control_user_role("bob", "admin")
ctrl.set_control_user_password("bob", "new_secret")
ctrl.delete_control_user("bob")
```

### Auth & security settings

```python
ctrl.auth_status()
ctrl.set_auth_password("new_control_password")
ctrl.enable_auth()
ctrl.disable_auth()

ctrl.data_security()
ctrl.set_data_security(protect_reads=True, disable_default_password=True)
```

### Misc

```python
ctrl.dashboard()           # full snapshot — DB info, server status, metrics, events
ctrl.db_settings()
ctrl.update_db_settings(name="MyDB")
ctrl.rate_limit_log(limit=100)
```

`MkDBController` raises `ControllerError` on API errors.

