import fetch from 'node-fetch';
import { MkDBResponse, MkDBConnection } from './types';

export class HttpConnection implements MkDBConnection {
    private baseUrl: string;
    private authHeader: string | null = null;

    constructor(
        host: string,
        port: number,
        username?: string,
        password?: string
    ) {
        this.baseUrl = `http://${host}:${port}`;
        if (username && password) {
            const auth = Buffer.from(`${username}:${password}`).toString('base64');
            this.authHeader = `Basic ${auth}`;
        }
    }

    async connect(): Promise<void> {
        const res = await fetch(`${this.baseUrl}/health`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
    }

    async send(payload: any): Promise<any> {
        const action = payload.action;
        let method = 'POST';
        let path = '/data';

        if (action === 'read') {
            method = 'GET';
            path = `/data/${payload.store}/${payload.record_id}`;
        } else if (action === 'delete') {
            method = 'DELETE';
            path = `/data/${payload.store}/${payload.record_id}`;
        } else if (action === 'query') {
            path = '/query';
        } else if (action === 'list_stores') {
            method = 'GET';
            path = '/data';
        }

        const headers: any = {};
        if (this.authHeader) headers['Authorization'] = this.authHeader;
        if (method === 'POST') headers['Content-Type'] = 'application/json';

        const res = await fetch(`${this.baseUrl}${path}`, {
            method,
            headers,
            body: method === 'POST' ? JSON.stringify(payload) : undefined
        });

        const result = (await res.json()) as MkDBResponse<any>;
        if (result.status === 'error') {
            return { status: 'error', error: result.message, http_code: result.code };
        }
        return { status: 'ok', data: result.data };
    }

    close() { }
}
