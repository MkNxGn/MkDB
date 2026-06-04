# MkDB Architecture and Internal Storage Principles

The `src/db` tree establishes the architecture mapping of MkDB.

## The Rolling Log Engine (`src/db/storage`)
MkDB utilizes a Custom `Log-Structured` Append-only file pipeline layout.
1. `LogManager`: Sequentially rolls over `store` data. Data commits map directly to the tail edge of a `.log` instance. Rollovers are monitored by configurable megabyte bounds (`file_config.file_size_split_trigger`).
2. `IndexManager`: Maps physical byte locations offset/size attributes to primary Keys (`record_id`).
3. `Compactor`: Garbage collection thread daemon purging tombstone indexes (`!`) and flattening redundant mutations natively back to physical blocks sequentially. 

## Caching Strategy (`src/db/cache`)
To alleviate the high threshold properties of physical I/O bound queries, the cache system leverages:
1. `RamCache`: Configurable `ttl` and node limits bounding LRU/LFU cache hits before querying the disk Index logic directly.
2. `WriteQueue`: Mitigating random high frequency write locks. Merges repeated node properties inside `debounce_window` threshold, pushing exactly 1 native flattened task directly to Storage/Disk per cycle chunk.

## Communication Modes (`src/server/coms`)
Sockets use a 4-Byte Big-Endian encoded frame header format for mapping WebSockets reliably inside dynamic length boundaries. HTTP handles direct request routing via Python standard library `urllib` & `BaseHTTPRequestHandler`. Both architectures expose common handler boundaries wrapping logical execution against backend Thread objects perfectly.
