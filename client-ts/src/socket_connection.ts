import * as net from 'net';
import { EventEmitter } from 'events';
import {
    MkDBResponse,
    GetResponse,
    WriteResponse,
    DeleteResponse,
    QueryResponse,
    ListStoresResponse,
    MkDBConnection
} from './types';

export class SocketConnection extends EventEmitter implements MkDBConnection {
    private socket: net.Socket | null = null;
    private buffer: Buffer = Buffer.alloc(0);
    private pendingRequests: Map<string, { resolve: Function, reject: Function }> = new Map();
    private connected: boolean = false;

    constructor(
        private host: string,
        private port: number,
        private timeout: number = 30000
    ) {
        super();
    }

    async connect(): Promise<void> {
        return new Promise((resolve, reject) => {
            this.socket = new net.Socket();
            this.socket.setTimeout(this.timeout);

            this.socket.connect(this.port, this.host, () => {
                this.connected = true;
                resolve();
            });

            this.socket.on('data', (chunk) => {
                this.buffer = Buffer.concat([this.buffer, chunk]);
                this.processBuffer();
            });

            this.socket.on('error', (err) => {
                this.connected = false;
                reject(err);
            });

            this.socket.on('close', () => {
                this.connected = false;
            });
        });
    }

    async send(payload: any): Promise<any> {
        if (!this.connected || !this.socket) {
            throw new Error("Socket not connected");
        }

        const requestId = payload.id || Math.random().toString(36).substring(7);
        payload.id = requestId;

        return new Promise((resolve, reject) => {
            this.pendingRequests.set(requestId, { resolve, reject });

            const json = JSON.stringify(payload);
            const message = Buffer.from(json);
            const header = Buffer.alloc(4);
            header.writeUInt32BE(message.length, 0);

            this.socket!.write(header);
            this.socket!.write(message);
        });
    }

    private processBuffer() {
        while (this.buffer.length >= 4) {
            const length = this.buffer.readUInt32BE(0);
            if (this.buffer.length < length + 4) break;

            const messageJson = this.buffer.slice(4, length + 4).toString();
            this.buffer = this.buffer.slice(length + 4);

            try {
                const response = JSON.parse(messageJson);
                if (response.type === 'response' && response.id) {
                    const pending = this.pendingRequests.get(response.id);
                    if (pending) {
                        this.pendingRequests.delete(response.id);
                        if (response.status === 'ok') {
                            pending.resolve(response);
                        } else {
                            pending.reject(new Error(response.error || "Unknown error"));
                        }
                    }
                } else if (response.type === 'update') {
                    this.emit('update', response);
                }
            } catch (e) {
                console.error("Failed to parse socket message", e);
            }
        }
    }

    close() {
        if (this.socket) {
            this.socket.destroy();
            this.socket = null;
        }
        this.connected = false;
    }
}
