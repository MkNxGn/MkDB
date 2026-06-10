/**
 * MkDB SDK Response interfaces.
 */

export interface MkDBResponse<T> {
    status: "ok" | "error";
    data?: T;
    message?: string;
    code?: number;
}

export interface GetResponse {
    record_id: string;
    data: any;
    found: boolean;
}

export interface WriteResponse {
    record_id: string;
    store: string;
}

export interface DeleteResponse {
    record_id: string;
    store: string;
}

export interface QueryResponse {
    count: number;
    total_matches: number;
    ids: string[];
    records?: any[];
}

export interface StoreInfo {
    name: string;
    record_count: number;
}

export type ListStoresResponse = StoreInfo[];

export interface DashboardResponse {
    db_info: any;
    server_info: any;
    stores: Record<string, any>;
    recent_events: any[];
}

export interface MkDBConnection {
    connect(): Promise<void>;
    send(payload: any): Promise<any>;
    close(): void;
    on?(event: string, listener: (...args: any[]) => void): any;
}


