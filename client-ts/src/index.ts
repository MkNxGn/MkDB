import {
    MkDBResponse,
    GetResponse,
    WriteResponse,
    DeleteResponse,
    QueryResponse,
    ListStoresResponse,
    MkDBConnection
} from './types';
import { HttpConnection } from './http_connection';
import { SocketConnection } from './socket_connection';

export { Q } from './query';
export { MkDBController } from './controller';
export * from './types';

export class MkDBError extends Error {
    constructor(public message: string, public code?: number) {
        super(message);
        this.name = "MkDBError";
    }
}

export type AccessMode = "R" | "W" | "RW";
export type TransportType = "http" | "socket";

export interface MkDBClientConfig {
    host?: string;
    port?: number;
    transport?: TransportType;
    username?: string;
    password?: string;
    trackRecords?: boolean;
}

export class MkDBClient {
    private _conn: MkDBConnection;
    private readonly trackRecords: boolean;

    constructor(config: MkDBClientConfig = {}) {
        const host = config.host || "127.0.0.1";
        const transport = config.transport || "socket";
        const port = config.port || (transport === "socket" ? 8000 : 80);
        this.trackRecords = config.trackRecords || false;

        if (transport === "socket") {
            this._conn = new SocketConnection(host, port);
        } else {
            this._conn = new HttpConnection(host, port, config.username, config.password);
        }
    }

    async connect() {
        await this._conn.connect();
    }

    private async request<T>(
        action: string,
        store: string,
        params: any = {}
    ): Promise<T> {
        const payload = {
            action,
            store,
            ...params
        };

        const result = await this._conn.send(payload);

        if (result.status === "error") {
            throw new MkDBError(result.error || "Unknown error", result.http_code);
        }

        return result.data as T;
    }

    /**
     * Ping the server.
     */
    async ping(): Promise<boolean> {
        try {
            await this.request("ping", "");
            return true;
        } catch {
            return false;
        }
    }

    /**
     * Get a record by ID.
     */
    async get(store: string, recordId: string): Promise<GetResponse> {
        try {
            const data = await this.request<any>("read", store, { record_id: recordId });

            let processedData = data;
            if (this.trackRecords && data) {
                processedData = this.wrapInSnapshot(store, recordId, data);
            }

            return {
                record_id: recordId,
                data: processedData,
                found: true
            };
        } catch (err) {
            if (err instanceof MkDBError && err.code === 404) {
                return { record_id: recordId, data: null, found: false };
            }
            throw err;
        }
    }

    /**
     * Store or update a record.
     */
    async set(store: string, recordId: string, data: any): Promise<WriteResponse> {
        await this.request("write", store, {
            record_id: recordId,
            delta: data
        });
        return { record_id: recordId, store };
    }

    /**
     * Delete a record.
     */
    async delete(store: string, recordId: string): Promise<DeleteResponse> {
        await this.request("delete", store, { record_id: recordId });
        return { record_id: recordId, store };
    }

    /**
     * Run a query on a store.
     */
    async query(
        store: string,
        filter: any = {},
        hydrate: boolean = true
    ): Promise<QueryResponse> {
        return await this.request<QueryResponse>("query", store, {
            filter,
            hydrate
        });
    }

    /**
     * List all available stores.
     */
    async listStores(): Promise<ListStoresResponse> {
        return await this.request<ListStoresResponse>("list_stores", "");
    }

    /**
     * Subscribe to updates on a store (Socket only).
     */
    async subscribe(store: string, callback: (event: any) => void) {
        if (this._conn.on) {
            await this._conn.send({ action: 'subscribe', store });
            this._conn.on('update', (event: any) => {
                if (event.store === store) callback(event);
            });
        } else {
            throw new Error("Subscriptions require socket transport");
        }
    }

    private wrapInSnapshot(store: string, recordId: string, data: any): any {
        return createSnapshot(this, store, recordId, data);
    }
}


/**
 * Snapshot tracking logic (Parity with Python SnapshotBaseObject)
 */
function createSnapshot(client: MkDBClient, store: string, recordId: string, initialData: any) {
    const original = JSON.parse(JSON.stringify(initialData));
    const current = JSON.parse(JSON.stringify(initialData));

    // Add .patch() method
    const proxy = new Proxy(current, {
        get(target, prop, receiver) {
            if (prop === 'patch') {
                return async () => {
                    const delta = calculateDelta(original, target);
                    if (Object.keys(delta).length > 0) {
                        await client.set(store, recordId, delta);
                        // After successful patch, update original to match current
                        Object.assign(original, JSON.parse(JSON.stringify(target)));
                    }
                };
            }
            return Reflect.get(target, prop, receiver);
        }
    });

    return proxy;
}

function calculateDelta(oldVal: any, newVal: any): any {
    if (oldVal === newVal) return undefined;
    if (typeof oldVal !== 'object' || oldVal === null || typeof newVal !== 'object' || newVal === null) {
        return newVal;
    }

    const delta: any = {};
    const allKeys = new Set([...Object.keys(oldVal), ...Object.keys(newVal)]);

    for (const key of allKeys) {
        const vOld = oldVal[key];
        const vNew = newVal[key];

        if (!(key in newVal)) {
            // Key deleted (in MkDB we use null/None to signal deletion in deep merge)
            delta[key] = null;
        } else if (!(key in oldVal)) {
            // New key added
            delta[key] = vNew;
        } else if (typeof vOld === 'object' && vOld !== null && typeof vNew === 'object' && vNew !== null) {
            // Nested object - recurse
            const d = calculateDelta(vOld, vNew);
            if (d !== undefined && Object.keys(d).length > 0) {
                delta[key] = d;
            }
        } else if (vOld !== vNew) {
            delta[key] = vNew;
        }
    }

    return Object.keys(delta).length > 0 ? delta : undefined;
}
