# MkDB Query Guide

MkDB supports a rich, declarative query language with support for filtering, sorting, and pagination.

## Basic Keywords
- `eq`: Exact match
- `neq`: Not equal
- `gt`, `gte`, `lt`, `lte`: Numeric comparisons
- `in`: Match any value in a list
- `nin`: Exclude if value is in list
- `contains`: Tokenized full-text search
- `exists`: Check if a field has a readable value

## Query Structure
Queries are JSON objects sent to the `query` method.

```json
{
  "filter": {
    "price": {"lt": 100},
    "category": "electronics"
  },
  "sort": "-price",
  "limit": 20,
  "offset": 40
}
```

## Python Query Builder (Q)
For rapid development, use the `Q` helper. The `MkDBClient.query` method accepts `Q` objects directly without needing to call `.build()`.

**Logical Operators:**
You can combine queries using `&` (AND) and `|` (OR).

```python
from mkdb_client import MkDBClient, Q

client = MkDBClient()

# Build and send fluently with logical branching
query = (
    Q.field("status").eq("active") & 
    (Q.field("category").eq("deals") | Q.field("price").lt(5))
).sort("-price").limit(20)

results = client.query("products", query)
print(f"Showing {results.count} of {results.total_matches} matches")
```

## Sorting
MkDB uses a prefix-based sorting system:
- **`+fieldname`**: Ascending order (lowest to highest).
- **`-fieldname`**: Descending order (highest to lowest).

Example: `Q.field("price").lt(100).sort("-price")` returns matches from $99.99 down to $0.

## Logical Operators
Use `$or` or `$and` for branching:

```python
results = client.query("products", {
    "filter": {
        "$or": [
            {"category": "deals"},
            {"price": {"lt": 5}}
        ]
    }
})
```

## Nested Object Queries
MkDB supports querying into nested JSON objects using dot-notation.

**Requirement:** This feature must be enabled in the store's configuration via `nested_queries_enabled: true`.

```python
# Querying a record like: {"user": {"profile": {"age": 25}}}
query = Q.field("user.profile.age").gte(21)
results = client.query("users", query)
```

> **Performance Note:** Nested queries use a "Full Scan" approach as they are not currently indexed. Use them sparingly on very large datasets or ensure you filter by indexed top-level fields first to reduce the scan size.

## Documentation Index
- [JSON API Specification](JSON_SPEC.md): How to format raw JSON requests.
- [SDK Reference](SDK_REFERENCE.md): Full client method documentation.

