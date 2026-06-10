# MkDB JavaScript & TypeScript Query Guide

Both the JavaScript and TypeScript clients provide a fluent `Q` builder for constructing MkDB queries. This builder allows you to chain filters, sorting, and pagination in a type-safe and readable way.

## Basic Usage

The `MkDBClient.query` method accepts either a raw filter object or a `Q` builder instance.

### Using the Builder (Recommended)

```javascript
// In Node.js or when using a bundler (Vite, Webpack)
import { MkDBClient, Q } from 'mkdb-client';

// In a browser using the standalone bundle
// const { MkDBClient, Q } = MkDB;

const client = new MkDBClient({ host: "127.0.0.1" });
await client.connect();

const query = Q.field("price").lt(100)
    .sort("-price")
    .limit(10)
    .offset(0);

const results = await client.query("products", query);
console.log(`Found ${results.total_matches} items`);
```

## Field Comparison Operators

The `Q.field(name)` method returns a proxy that provides several comparison methods:

| Method | Description | Example |
| :--- | :--- | :--- |
| `.eq(val)` | Equals | `Q.field("status").eq("active")` |
| `.neq(val)` | Not equal | `Q.field("type").neq("internal")` |
| `.gt(val)` | Greater than | `Q.field("age").gt(18)` |
| `.gte(val)` | Greater than or equal | `Q.field("price").gte(10)` |
| `.lt(val)` | Less than | `Q.field("stock").lt(5)` |
| `.lte(val)` | Less than or equal | `Q.field("price").lte(100)` |
| `.contains(val)` | Full-text search | `Q.field("description").contains("bolted")` |
| `.in(values[])` | Match any in list | `Q.field("category").in(["electronics", "books"])` |
| `.exists(bool)` | Field exists | `Q.field("deleted_at").exists(false)` |

## Combining Filters (AND / OR)

Unlike the Python SDK which uses operator overloading (`&` and `|`), the JS/TS clients use explicit `.and()` and `.or()` methods.

### Logical AND

```javascript
// status is 'active' AND price is less than 50
const query = Q.field("status").eq("active")
    .and(Q.field("price").lt(50));
```

### Logical OR

```javascript
// category is 'deals' OR price is less than 5
const query = Q.field("category").eq("deals")
    .or(Q.field("price").lt(5));
```

### Complex Branching

You can nest logical operations to form complex queries.

```javascript
const query = Q.field("status").eq("active")
    .and(
        Q.field("category").eq("deals")
        .or(Q.field("price").lt(10))
    );
```

## Sorting, Limits, and Offsets

The query builder supports the same sorting and pagination flags as the core engine.

- **`.sort(fieldWithPrefix)`**: Use `+` for ascending and `-` for descending.
- **`.limit(count)`**: Limit the number of records returned.
- **`.offset(count)`**: Skip a number of records (for pagination).

```javascript
const query = Q.field("category").eq("books")
    .sort("-rating")   // Highest rated first
    .limit(20)
    .offset(40);       // Page 3
```

## Raw Filter Objects

If you prefer not to use the builder, you can pass a standard MkDB filter object directly.

```javascript
const results = await client.query("products", {
    "price": { "lt": 100 },
    "brand": "Acme"
});
```

## Query Response Structure

The `query` method returns a `QueryResponse` object:

```typescript
interface QueryResponse {
    ids: string[];          // List of matching record IDs
    data: any[];           // List of full records (if hydrate: true)
    count: number;         // Number of items in this response
    total_matches: number; // Total matches on the server
}
```

By default, `hydrate` is `true`, meaning the `data` array will contain the full record contents. If you only need the IDs, pass `false` as the third argument to `query`.

```javascript
const idsOnly = await client.query("products", query, false);
```
