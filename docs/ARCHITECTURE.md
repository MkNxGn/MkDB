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
MkDB supports both HTTP and a custom Binary Socket protocol.

### Multiplexed Socket Protocol
The socket server (`SocketServer`) uses a 4-Byte Big-Endian encoded frame header format. The protocol is fully **Multiplexed**, meaning:
1. **Parallel Dispatch:** Requests are immediately acknowledged with a `receipt` and handed off to a `ThreadPoolExecutor` worker pool.
2. **Out-of-Order Responses:** Multiple requests can be active on a single socket. If a slow query is running, small reads can complete and return responses ahead of it.
3. **Receipt Handshake:** Every request gets a fast "Receipt ACK" within milliseconds. This allows the client to detect dead connections instantly if the ACK is missing.
4. **Thread-Safe I/O:** Both client and server use per-session write-locks to ensure interleaved frames are binary-safe.

### HTTP Control API
HTTP handles direct request routing via Python standard library `urllib` & `BaseHTTPRequestHandler`. Both architectures expose common handler boundaries wrapping logical execution against backend Thread objects perfectly.
