# MkDB

MkDB is a log-structured, partitioned NoSQL document database built entirely in Python. It provides a robust, self-healing storage engine with full-text search, numeric indexes, and dual-protocol network access — all manageable through an embedded web control panel.

## Architecture Highlights

- **Rolling Log Storage Engine**: Append-only storage with safe background compaction.
- **RAM Cache & Debounced Write Queue**: In-memory caching and write batching for high write throughput.
- **Query Engine & Secondary Indexes**: Numeric range queries and tokenized full-text inverted indexes.
- **Data Integrity**: Reed-Solomon parity encoding for proactive self-healing.
- **Dual Protocols**: Persistent TCP socket (with pub-sub) or standard HTTP REST.
- **Web Administration UI**: Embedded control panel at `/control` — manage stores, users, schema, metrics, and server settings.

## Project Structure

- `mkdb/db/`: Core database engine — storage primitives, RAM cache, query engine, compaction, parity.
- `mkdb/server/`: TCP socket server, HTTP data-plane server, and embedded web control panel.
- `mkdb/config/`: Configuration schemas for stores, memory limits, storage thresholds, and server bindings.
- `client/`: The `pymkdb-client` package — a lightweight, stdlib-only Python SDK.

---

## Installation

### Server

```bash
pip install PyMkDB
```

### Client (separate package)

```bash
pip install pymkdb-client
```

---

## Getting Started

### Starting the Server

MkDB is a CLI tool. Point it at a directory containing a `config.json`:

```bash
mkdb /path/to/your/db
```

To generate a default `config.json` in a new directory:

```bash
mkdb /path/to/your/db -c
```

Once running, the web control panel is available at `http://localhost:<control_port>`.

---

## Python Client (`pymkdb-client`)

### Basic usage

```python
from mkdb_client import MkDBClient

# transport="socket" (default, supports pub-sub) or transport="http"
client = MkDBClient(host="127.0.0.1", port=9001, access="RW", password="mk_db")
client.connect()

# Write a record
client.set("products", "prod_001", {"name": "Steel Bolt", "price": 9.99})

# Insert with a server-generated ID
resp = client.insert("products", {"name": "Widget", "price": 4.99})
print(resp.record_id)

# Read a record
resp = client.get("products", "prod_001")
if resp.found:
    print(resp.data)

# Query with filters
resp = client.query("products", {"price": {"lte": 10.00}})
print(resp.count, resp.ids)

# Query and return full records
resp = client.query("products", {"name": ["bolt"]}, hydrate=True)
print(resp.records)

# Delete a record
client.delete("products", "prod_001")

# Subscribe to live updates (socket transport only)
client.on_update("products", lambda event: print("Change:", event))

client.close()
```

### Error handling

```python
from mkdb_client import (
    MkDBConnectionError,
    MkDBAuthError,
    MkDBTimeoutError,
    MkDBStoreNotFoundError,
    MkDBRecordNotFoundError,
    MkDBStoreExistsError,
    MkDBQueryError,
    MkDBServerError,
)

try:
    resp = client.get("products", "prod_001")
except MkDBRecordNotFoundError:
    print("Record does not exist")
except MkDBStoreNotFoundError:
    print("Store does not exist")
except MkDBQueryError:
    print("Invalid query filter")
except MkDBAuthError:
    print("Authentication failed")
except MkDBTimeoutError:
    print("Request timed out")
except MkDBConnectionError:
    print("Could not reach server")
except MkDBServerError as e:
    print("Server error:", e)
```

### Control-plane client (`MkDBController`)

`MkDBController` connects to the control-plane HTTP API to manage the database programmatically:

```python
from mkdb_client import MkDBController

ctrl = MkDBController(host="127.0.0.1", port=8090)
ctrl.login(username="admin", password="secret")

# Store management
ctrl.create_store("orders", description="Customer orders")
ctrl.list_stores()
ctrl.delete_store("old_store")

# Server control
ctrl.server_status()
ctrl.server_stop("http")
ctrl.server_start("http")

ctrl.logout()
```

---

See the `docs/` folder for the full Query Syntax reference and SDK documentation.

