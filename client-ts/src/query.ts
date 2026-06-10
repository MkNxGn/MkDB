/**
 * MkDB Query Builder — a fluent interface for building complex MkDB queries.
 */

export class Q {
    constructor(
        private _filter: any = {},
        private _sort: string | null = null,
        private _limit: number | null = null,
        private _offset: number | null = null
    ) { }

    /**
     * Start a query for a specific field.
     */
    static field(name: string): FieldProxy {
        return new FieldProxy(name);
    }

    /**
     * Set sorting, e.g., .sort("-price") or .sort("+name")
     */
    sort(fieldWithPrefix: string): Q {
        this._sort = fieldWithPrefix;
        return this;
    }

    /**
     * Set result limit.
     */
    limit(count: number): Q {
        this._limit = count;
        return this;
    }

    /**
     * Set result offset.
     */
    offset(count: number): Q {
        this._offset = count;
        return this;
    }

    /**
     * Combine two queries with AND logic.
     */
    and(other: Q): Q {
        return new Q({ "$and": [this._filter, (other as any)._filter] });
    }

    /**
     * Combine two queries with OR logic.
     */
    or(other: Q): Q {
        return new Q({ "$or": [this._filter, (other as any)._filter] });
    }

    /**
     * Legacy support/simple where clause
     */
    static where(field: string, op: string, value: any): Q {
        const f: any = {};
        f[field] = { [op]: value };
        return new Q(f);
    }

    build(): any {
        const q: any = { filter: this._filter };
        if (this._sort !== null) q.sort = this._sort;
        if (this._limit !== null) q.limit = this._limit;
        if (this._offset !== null) q.offset = this._offset;
        return q;
    }
}

class FieldProxy {
    constructor(private _name: string) { }

    eq(val: any): Q { return new Q({ [this._name]: { "eq": val } }); }
    neq(val: any): Q { return new Q({ [this._name]: { "neq": val } }); }
    gt(val: number): Q { return new Q({ [this._name]: { "gt": val } }); }
    gte(val: number): Q { return new Q({ [this._name]: { "gte": val } }); }
    lt(val: number): Q { return new Q({ [this._name]: { "lt": val } }); }
    lte(val: number): Q { return new Q({ [this._name]: { "lte": val } }); }
    contains(val: string): Q { return new Q({ [this._name]: { "contains": val } }); }
    in(vals: any[]): Q { return new Q({ [this._name]: { "in": vals } }); }
    exists(val: boolean = true): Q { return new Q({ [this._name]: { "exists": val } }); }
