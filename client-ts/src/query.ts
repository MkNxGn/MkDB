/**
 * Simple Query Builder for MkDB (TypeScript version)
 */
export class Q {
    private filter: any = {};

    static where(field: string, op: string, value: any): Q {
        const q = new Q();
        q.filter[field] = { [op]: value };
        return q;
    }

    and(field: string, op: string, value: any): Q {
        this.filter[field] = { [op]: value };
        return this;
    }

    build(): any {
        return this.filter;
    }
}
