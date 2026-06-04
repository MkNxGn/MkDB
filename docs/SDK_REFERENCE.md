# MkDB Python SDK Reference

The `sdk/` directory offers a standard `MkDBClient` interface to communicate with the DB engine predictably over either TCP Sockets or HTTP.

## Installation / Setup
Copy the `sdk/` folder into your working Python project or import it directly if within the same working directory.

```python
from sdk.mkdb_client import MkDBClient
```

## Initiating the Client
```python
client = MkDBClient()

# Connects to persistent TCP WebSocket frame handler, drops back gracefully to stateless HTTP 
client.connect("127.0.0.1", port=8080, use_socket=True)
```

## Core Methods

### `client.get(store: str, record_id: str) -> dict`
Fetches a record by ID.
Checks the client's internal memory mirror first. If the record isn't found or has drifted, queries the DB backend and stores the result locally.

### `client.set(store: str, record_id: str, data: dict)`
Upserts a document. 
Under the hood, MkDB's SDK computes the delta (changes) between `data` and what is already cached locally. It then constructs a flattened `.dot` syntax operation and transmits *only the strictly changed delta*, hugely optimizing network and I/O.

### `client.delete(store: str, record_id: str)`
Sends a deletion instruction mapped to the `record_id`.

### `client.query(store: str, filter: dict) -> list[dict]`
Fires an advanced query search based on unstructured index scanning or B-tree numeric bounds limits. Returns list of fully hydrated document dictionaries.

### `client.on_update(callback: Callable)`
Allows registration of reactive event streams to changes. Valid only if `use_socket=True` and connection passes.

```python
def my_listener(event):
    print(f"Update caught for: {event['record_id']} on {event['store']}")

client.on_update(my_listener)
```

## Security & Authentication Add-on
MkDB's Auth mechanics are constructed heavily client-side to enforce privacy principles. Storage logic is fully detached from user authentication rules.

```python
client.auth.register(username="john_doe", password="super_secret_password")

is_valid = client.auth.validate(username="john_doe", password="super_secret_password") 
# is_valid = True
```
The SDK handles all hashing inherently (salt generation + PBKDF2/Sha256 mechanics) transmitting the derived keys to the `users` backend store automatically.
