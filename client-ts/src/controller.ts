import fetch from 'node-fetch';
import { MkDBResponse, DashboardResponse } from './types';
import { MkDBError } from './index';

class SubController {
    constructor(protected parent: MkDBController) {}
    protected request<T>(path: string, body?: any): Promise<T> {
        return this.parent['request'](path, body);
    }
}

class DataUsersController extends SubController {
    async list() { return this.request<any[]>('/api', { action: 'api_list_users' }); }
    async create(username, password) { return this.request('/api', { action: 'api_create_user', username, password }); }
    async delete(username) { return this.request('/api', { action: 'api_delete_user', username }); }
    async setPassword(username, password) { return this.request('/api', { action: 'api_set_user_password', username, password }); }
    async setStoreAccess(username, store, read = true, write = false) { 
        return this.request('/api', { action: 'api_set_user_store_access', username, store, read, write }); 
    }
}

class ControlUsersController extends SubController {
    async list() { return this.request<any[]>('/api', { action: 'api_list_control_users' }); }
    async create(username, password, role = 'viewer') { 
        return this.request('/api', { action: 'api_create_control_user', username, password, role }); 
    }
    async setRole(username, role) { return this.request('/api', { action: 'api_set_control_user_role', username, role }); }
}

class SecurityController extends SubController {
    public users = new DataUsersController(this.parent);
    public rbac = new ControlUsersController(this.parent);
    async getSettings() { return this.request('/api', { action: 'api_get_data_security' }); }
    async updateSettings(config) { return this.request('/api', { action: 'api_set_data_security', ...config }); }
}

class StoresController extends SubController {
    async list() { return (await this.request<any>('/api', { action: 'api_list_stores' })).stores; }
    async create(name, description = '') { return this.request('/api', { action: 'api_create_store', name, description }); }
    async delete(name) { return this.request('/api', { action: 'api_delete_store', name }); }
    async getConfig(name) { return this.request('/api', { action: 'api_get_store_config', name }); }
    async updateConfig(name, config) { return this.request('/api', { action: 'api_update_store_config', name, ...config }); }
    async getMetrics(name) { return this.request('/api', { action: 'api_get_store_metrics', name }); }
    async restartWorkers(name) { return this.request('/api', { action: 'api_restart_workers', name }); }
}

class SystemController extends SubController {
    async getStatus() { return this.request('/api', { action: 'api_get_server_status' }); }
    async controlServer(server, op) { return this.request('/api', { action: 'api_server_control', server, op }); }
    async getEventLog(limit = 50, level = '') {
        const resp = await this.request<any>('/api', { action: 'api_get_event_log', limit, level });
        return resp.events;
    }
}

export class MkDBController {
    private baseUrl: string;
    private token: string | null = null;
    public role: string | null = null;

    public stores = new StoresController(this);
    public security = new SecurityController(this);
    public system = new SystemController(this);

    constructor(host: string = '127.0.0.1', port: number = 8090) {
        this.baseUrl = http://\System.Management.Automation.Internal.Host.InternalHost:\;
    }

    private async request<T>(path: string, body?: any, useToken: boolean = true): Promise<T> {
        const headers: any = { 'Content-Type': 'application/json' };
        if (useToken && this.token) headers['Authorization'] = Bearer \;
        const res = await fetch(\\, { method: 'POST', headers, body: JSON.stringify(body || {}) });
        const result = (await res.json()) as any;
        if (result.status === 'error') throw new MkDBError(result.message || 'Error', result.code);
        return (result.result !== undefined ? result.result : result.data) as T;
    }

    async login(username, password) {
        const resp = await this.request<any>('/control/auth', { username, password }, false);
        this.token = resp.token;
        this.role = resp.role || 'viewer';
        return this.role;
    }

    async logout() {
        if (this.token) {
            try { await this.request('/auth/logout', {}); } catch (e) {}
            this.token = null; this.role = null;
        }
    }

    async dashboard() { return this.request('/api', { action: 'api_get_dashboard' }); }
}
