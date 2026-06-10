# MkDB JSON Query Specification

This guide defines how to structure JSON payloads for queries when using the raw API or non-Python clients.

## High-Level Structure
A query request is a JSON object with the following top-level keys:

| Key | Type | Description |
| :--- | :--- | :--- |
| `filter` | `object` | The selection criteria (see Operators below). |
| `sort` | `string` | Field prefixed with `+` (asc) or `-` (desc). e.g., `"-created_at"`. |
| `limit` | `number` | Maximum records to return. Default: `300`. |
| `offset` | `number` | Number of records to skip for pagination. |
| `hydrate` | `boolean`| If `true`, returns full record data; if `false`, returns IDs only. |

### Example Payload
```json
{
  "filter": {
    "status": "active",
    "price": { "lt": 100 }
  },
  "sort": "-price",
  "limit": 50,
  "hydrate": true
}
```

---

## Filter Operators

### 1. Comparison Operators
Used for numeric values or exact string matches.
- `eq`: Equal to.
- `neq`: Not equal to.
- `gt` / `gte`: Greater than / Greater than or equal.
- `lt` / `lte`: Less than / Less than or equal.

### 2. Collection Operators
- `in` / `is_included`: Matches if the field value is present in the provided list.
- `nin` / `not_included`: Matches if the field value is NOT in the provided list.

### 3. Text & Content Operators
- `contains`: Tokenized search. Matches if the field contains the words in the query string.
- `exists`: (Boolean) Matches if the field is not `null`, `""`, or `[]`. 

---

## Logical Branching
You can combine multiple filters into complex groups.

### Logical AND (Default)
All fields at the same level are combined with AND.
```json
{
  "category": "books",
  "in_stock": true
}
```

### Logical OR
Matches if ANY of the sub-filters match.
```json
{
  "filter": {
    "$or": [
      { "category": "books" },
      { "promotion": "clearance" }
    ]
  }
}
```

## Response Format
The server returns a JSON object with:
- `ids`: Array of matching record IDs.
- `records`: Array of full record objects (populated only if `hydrate: true`).
- `count`: Number of items returned in the current page.
- `total_matches`: Total number of items matching the filter across the whole database.
