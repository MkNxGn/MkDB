/**
 * MkDB Client - Standalone JavaScript Version
 * Compatible with Browsers and Node.js
 */

(function (root, factory) {
    if (typeof define === 'function' && define.amd) {
        define([], factory);
    } else if (typeof module === 'object' && module.exports) {
        module.exports = factory();
    } else {
        root.MkDB = factory();
    }
}(typeof self !== 'undefined' ? self : this, function () {

    class MkDBError extends Error {
        constructor(message, code) {
            super(message);
            this.name = "MkDBError";
            this.code = code;
        }
    }

    class Q {
        constructor() {
            this.filter = {};
        }
        static where(field, op, value) {
            const q = new Q();
            q.filter[field] = { [op]: value };
            return q;
        }
        and(field, op, value) {
            this.filter[field] = { [op]: value };
            return this;
        }
        build() {
            return this.filter;
        }
    }

    class MkDBClient {
        constructor(config = {}) {
            const host = config.host || "127.0.0.1";
            const port = config.port || 80;
            this.baseUrl = `http://${host}:${port}`;
            this.trackRecords = config.trackRecords || false;
            this.authHeader = null;

            if (config.username && config.password) {
                const raw = `${config.username}:${config.password}`;
                // Browser & Node 16+ support btoa/atob
                if (typeof btoa === 'function') {
                    this.authHeader = `Basic ${btoa(raw)}`;
                } else {
                    this.authHeader = `Basic ${Buffer.from(raw).toString('base64')}`;
                }
            }
        }

        async request(method, path, body) {
            const headers = {};
            if (this.authHeader) {
                headers["Authorization"] = this.authHeader;
            }
            if (body) {
                headers["Content-Type"] = "application/json";
            }

            const options = {
                method,
                headers,
                body: body ? JSON.stringify(body, (key, value) => {
                    if (value instanceof Set) return Array.from(value);
                    return value;
                }) : undefined
            };

            // Use global fetch
            const response = await fetch(`${this.baseUrl}${path}`, options);
            const result = await response.json();

            if (result.status === "error") {
                throw new MkDBError(result.message || "Unknown error", result.code);
            }

            return result.data;
        }

        async ping() {
            try {
                await this.request("GET", "/health");
                return true;
            } catch {
                return false;
            }
        }

        async get(store, recordId) {
            try {
                const data = await this.request("GET", `/data/${store}/${recordId}`);
                let processedData = data;
                if (this.trackRecords && data) {
                    processedData = this.wrapInSnapshot(store, recordId, data);
                }
                return { record_id: recordId, data: processedData, found: true };
            } catch (err) {
                if (err instanceof MkDBError && err.code === 404) {
                    return { record_id: recordId, data: null, found: false };
                }
                throw err;
            }
        }

        async set(store, recordId, data) {
            await this.request("POST", "/data", {
                store,
                record_id: recordId,
                delta: data
            });
            return { record_id: recordId, store };
        }

        async delete(store, recordId) {
            await this.request("DELETE", `/data/${store}/${recordId}`);
            return { record_id: recordId, store };
        }

        async query(store, filter = {}, hydrate = true) {
            return await this.request("POST", "/query", { store, filter, hydrate });
        }

        async listStores() {
            return await this.request("GET", "/data");
        }

        wrapInSnapshot(store, recordId, data) {
            const client = this;
            const original = JSON.parse(JSON.stringify(data));
            const current = JSON.parse(JSON.stringify(data));

            return new Proxy(current, {
                get(target, prop, receiver) {
                    if (prop === 'patch') {
                        return async () => {
                            const delta = calculateDelta(original, target);
                            if (delta && Object.keys(delta).length > 0) {
                                await client.set(store, recordId, delta);
                                Object.assign(original, JSON.parse(JSON.stringify(target)));
                            }
                        };
                    }
                    return Reflect.get(target, prop, receiver);
                }
            });
        }
    }

    function calculateDelta(oldVal, newVal) {
        if (oldVal === newVal) return undefined;
        if (typeof oldVal !== 'object' || oldVal === null || typeof newVal !== 'object' || newVal === null) {
            return newVal;
        }

        const delta = {};
        const allKeys = new Set([...Object.keys(oldVal), ...Object.keys(newVal)]);

        for (const key of allKeys) {
            const vOld = oldVal[key];
            const vNew = newVal[key];

            if (!(key in newVal)) {
                delta[key] = null;
            } else if (!(key in oldVal)) {
                delta[key] = vNew;
            } else if (typeof vOld === 'object' && vOld !== null && typeof vNew === 'object' && vNew !== null) {
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

    return { MkDBClient, Q, MkDBError };
}));
