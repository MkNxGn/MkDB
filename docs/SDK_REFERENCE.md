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

`transport="socket"` opens a persistent TCP connection and supports pub-sub (`on_update`).  
`transport="http"` makes a fresh HTTP request per call — no pub-sub support.

---

### `client.get(store, record_id) → GetResponse`

Read a single record by ID.

```python
resp = client.get("products", "prod_001")
if resp.found:
    print(resp.data)   # dict of field values
```

| Attribute | Type | Description |
|---|---|---|
| `record_id` | `str` | The ID that was requested |
| `data` | `dict \| None` | Record fields, or `None` if not found |
| `found` | `bool` | `True` if the record exists |

`GetResponse` is truthy when `found=True`.

---

### `client.set(store, record_id, delta, flatten_nested=True) → WriteResponse`

Write or update a record. Only the supplied fields are changed (delta write).

```python
resp = client.set("products", "prod_001", {"name": "Steel Bolt", "price": 9.99})
print(resp.record_id)
```

When `flatten_nested=True` (default), nested dicts are automatically flattened to dot-notation keys before sending:

```python
client.set("products", "prod_001", {"meta": {"colour": "silver"}})
# sent as: {"meta.colour": "silver"}
```

---

### `client.insert(store, delta, flatten_nested=True) → WriteResponse`

Write a new record with a **server-generated ID**.

```python
resp = client.insert("products", {"name": "Widget", "price": 4.99})
print(resp.record_id)  # e.g. "aB3xKq7mNpRt"
```

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

### `client.query(store, filter_dict, hydrate=False) → QueryResponse`

Query a store using a filter dict. Returns matching record IDs by default, or full records when `hydrate=True`.

```python
# Returns IDs only
resp = client.query("products", {"price": {"lte": 10.00}})
print(resp.count, resp.ids)

# Returns full records
resp = client.query("products", {"name": ["bolt", "screw"]}, hydrate=True)
for record in resp:   # iterates records when hydrated, IDs otherwise
    print(record)
```

**Filter syntax:**

| Filter | Meaning |
|---|---|
| `{"field": "exact"}` | Exact string match |
| `{"field": ["kw1", "kw2"]}` | Full-text AND — both keywords must appear |
| `{"field": 42}` | Numeric exact match |
| `{"field": {"gt": 5, "lte": 20}}` | Numeric range (`gt`, `gte`, `lt`, `lte`) |
| Multiple keys | AND — all conditions must match |

`QueryResponse` attributes: `count`, `ids`, `records` (only when `hydrate=True`), `store`.  
It is truthy when `count > 0` and supports `len()` and iteration.

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

