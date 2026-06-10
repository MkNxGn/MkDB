import fetch from 'node-fetch';
import { MkDBResponse, DashboardResponse } from './types';
import { MkDBError } from './index';

export class MkDBController {
    private baseUrl: string;
    private token: string | null = null;
    public role: string | null = null;

    constructor(host: string = "127.0.0.1", port: number = 8090) {
        this.baseUrl = `http://${host}:${port}`;
    }

    private async request<T>(path: string, body?: any, useToken: boolean = true): Promise<T> {
        const headers: Record<string, string> = {
            'Content-Type': 'application/json'
        };

        if (useToken && this.token) {
            headers['Authorization'] = `Bearer ${this.token}`;
        }

        const res = await fetch(`${this.baseUrl}${path}`, {
            method: 'POST',
            headers,
            body: JSON.stringify(body || {})
        });

        const result = (await res.json()) as MkDBResponse<T>;

        if (result.status === "error") {
            throw new MkDBError(result.message || "Control plane error", result.code);
        }

        return result.data as T;
    }

    async login(username: string, password: string): Promise<string> {
        const resp = await this.request<{ token: string, role: string }>('/control/auth', { username, password }, false);
        this.token = resp.token;
        this.role = resp.role || "viewer";
        return this.role;
    }

    async logout(): Promise<void> {
        if (this.token) {
            try {
                await this.request('/auth/logout', {});
            } catch (e) { }
            this.token = null;
            this.role = null;
        }
    }

    async dashboard(): Promise<DashboardResponse> {
        return await this.request<DashboardResponse>('/api', { action: 'api_get_dashboard' });
    }

    async listStores(): Promise<any[]> {
        return await this.request<any[]>('/api', { action: 'api_list_stores' });
    }

    async createStore(name: string, config: any = {}): Promise<void> {
        await this.request('/api', {
            action: 'api_create_store',
            name,
            config
        });
    }

    async deleteStore(name: string): Promise<void> {
        await this.request('/api', {
            action: 'api_delete_store',
            name
        });
    }

    async getEventLog(limit: number = 50, level: string = ""): Promise<any[]> {
        const data: any = { action: 'api_get_event_log', limit };
        if (level) data.level = level;
        const resp = await this.request<{ events: any[] }>('/api', data);
        return resp.events;
    }

    // --- Store Config & Metrics ---
    async getStoreConfig(name: string): Promise<any> {
        return await this.request('/api', { action: 'api_get_store_config', name });
    }

    async updateStoreConfig(name: string, config: any): Promise<any> {
        return await this.request('/api', { action: 'api_update_store_config', name, ...config });
    }

    async getStoreMetrics(name: string): Promise<any> {
        return await this.request('/api', { action: 'api_get_store_metrics', name });
    }

    async getAllStoreMetrics(): Promise<any[]> {
        return await this.request('/api', { action: 'api_get_all_store_metrics' });
    }

    // --- Server Control ---
    async getServerStatus(): Promise<any> {
        return await this.request('/api', { action: 'api_get_server_status' });
    }

    async controlServer(server: 'socket' | 'http' | 'control', op: 'start' | 'stop' | 'restart'): Promise<any> {
        return await this.request('/api', { action: 'api_server_control', server, op });
    }

    // --- Data-plane Users ---
    async listUsers(): Promise<any[]> {
        return await this.request('/api', { action: 'api_list_users' });
    }

    async createUser(username: string, password: string): Promise<any> {
        return await this.request('/api', { action: 'api_create_user', username, password });
    }

    async setUserStoreAccess(username: string, store: string, read: boolean = true, write: boolean = false): Promise<any> {
        return await this.request('/api', { action: 'api_set_user_store_access', username, store, read, write });
    }

    // --- Control-plane Users (RBAC) ---
    async listControlUsers(): Promise<any[]> {
        return await this.request('/api', { action: 'api_list_control_users' });
    }

    async createControlUser(username: string, password: string, role: 'viewer' | 'operator' | 'admin' = 'viewer'): Promise<any> {
        return await this.request('/api', { action: 'api_create_control_user', username, password, role });
    }
}

