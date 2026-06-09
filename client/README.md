# MkDB Python Client SDK

Persistent TCP socket and HTTP client for MkDB.

## Features
- **Multiplexed Socket Protocol**: Parallel async queries over a single connection.
- **Fast Reconnect**: Automatic dead-line detection and reconnection.
- **Interactive CLI**: `mkdb-client -i` for a live shell.

## Installation
```bash
pip install pymkdb-client
```

## Quick Start
```python
from mkdb_client import MkDBClient

client = MkDBClient(host="127.0.0.1", port=9001)
client.connect()

# Synchronous
res = client.get("my_store", "doc_1")

# Asynchronous
task = client.query_async("my_store", {"price": {"lt": 50}})
results = task.result()

client.close()
```
