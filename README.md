# MkDB

MkDB is a Custom Log-Structured Merge & Partitioned Redundant Storage Engine built entirely in Python. It provides a robust, highly-available NoSQL/Document database experience with secondary indexing, full-text search, and dual-protocol network access.

## Architecture Highlights

- **Rolling Log Storage Engine**: Append-only storage format guaranteeing high write availability with safe background compaction.
- **RAM Cache & Debounced Write Queue**: In-memory caching and debounced batching for extreme performance under high write load.
- **Query Engine & Secondary Indexes**: Fully featured query evaluation including numeric range checks and tokenized full-text inverted indexes.
- **Data Integrity**: Multi-disk mirroring and Reed-Solomon parity encoding for proactive self-healing and failover.
- **Dual Protocols**: Accessible via high-speed, persistent TCP WebSockets or standard stateless REST HTTP endpoints.
- **Web Administration UI**: Includes an embedded web control panel out-of-the-box (`/control` endpoint).

## Project Structure

- `src/db/`: The core database engine (storage primitives, RAM caching, query evaluator, auto-compaction and parity management).
- `src/server/`: The networking boundary. Houses the TCP Socket and HTTP REST servers, as well as the web-based Control Panel.
- `src/config/`: Configuration schemas for tailoring memory limits, storage thresholds, and cluster layout.
- `sdk/`: The official `MkDBClient` for programmatic interaction from Python code.

## Getting Started

### Prerequisites
- Python 3.10+
- The database storage format is built into MkDB natively, but you'll need the following for advanced data integrity features (Reed-Solomon logic):
  ```bash
  pip install reedsolo
  ```

### Starting the Server
MkDB operates as a CLI tool. Launch the engine by pointing it to your desired database directory (which must contain a `config.json` file configuring your stores and network bindings):
```bash
python mkdb.py /path/to/your/db
```
Once running, the database will host both TCP socket and HTTP interfaces as specified in your `config.json`. The web control panel is accessible via your browser (check server output for the bound port, normally `http://localhost:<port>`).

### Using the Python SDK
The `MkDBClient` connects seamlessly to your database and abstracts the dual-protocol system:

```python
from sdk.mkdb_client import MkDBClient

client = MkDBClient()
client.connect(host="127.0.0.1", port=8080)

# Writing a document (computes delta updates intelligently)
client.set(
    store="products",
    record_id="prod_001",
    data={"name": "Steel Bolt", "price": 9.99, "category": "fasteners"}
)

# Reading a document
record = client.get("products", "prod_001")

# Querying with filters
results = client.query("products", filter={
    "price": {"<=": 10.00},
    "category": ["fasteners"]
})
```

See the `docs/` folder for comprehensive guides on the Query Syntax and SDK Reference.
