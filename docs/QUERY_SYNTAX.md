# MkDB Query Syntax

The MkDB `QueryEngine` parses a declarative JSON mapping to perform unstructured text and relational numeric queries safely and quickly. This document outlines the filter syntax natively supported by the HTTP `/query` endpoint, Socket protocol, and the `MkDBClient.query()` Python SDK method.

## Field Matching Operations

### 1. Primary ID Filtering (Fast Path)
The fastest type of query bypasses secondary indexes and retrieves documents via the primary B-tree in `O(1)` or `O(N)` for explicit lists.
- **Exact Single ID:** `{"id": "user123"}`
- **List of IDs:** `{"id": ["user123", "user456"]}` (Array match is ONLY supported for the Primary `id` field).

### 2. Full-Text Search (Unstructured Text)
- **Exact String match:** `{"description": "exact words match"}` (Evaluated dynamically via linear scan. Precise but unindexed).
- **Tokenised Search (Single Keyword):** `{"description": ["steel"]}` (Uses the inverted index. Tokenises the word and retrieves immediately).
- **Tokenised Search (Multi-Keyword Intersect):** `{"description": ["steel", "bolt"]}` (Searches the index for both tokens independently and `AND` intersects the specific document IDs).

### 3. Numeric Types (Relational Boundaries)
- **Exact Match:** `{"year": 2024}`
- **Range Queries:** Provide a nested dictionary with relational operators.
  ```json
  {"year": {">=": 2020, "<": 2025}}
  ```
  **Supported operators:** `>=`, `<=`, `>`, `<`. Note that MkDB accumulates the dictionary constraints and performs a `AND` logical intersection effectively yielding closed ranges.

## Compound Intersect Queries

By providing multiple fields at the top level of the payload, the query acts as a strict `AND` evaluation. A document MUST satisfy EVERY criteria to be included in the return list.

**Example Compound Query:**
```json
{
    "year":        {">=": 2020, "<": 2025},
    "description": ["engine", "replacement"],
    "category":    "automotive"
}
```

## Hydration Flag (`hydrate`)
By default, queries against bare REST endpoints or WS payloads may return simply a list of matching `[ID1, ID2, ID3]`. In both protocol forms (HTTP json and SDK calls), passing `hydrate=True` (or passing `hydrate: true` JSON payload parameter) commands the engine to look up and dynamically return the full dictionaries corresponding to each `record_id` array index, avoiding the `N+1` problem.
