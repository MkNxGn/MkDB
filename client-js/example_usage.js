import { MkDBClient } from './mkdb-client.js';

// The browser version uses the precompiled bundle which only supports HTTP (renamed to Socket in your request, 
// but technically browsers can only do HTTP/WebSockets).
// However, since we want a "JS version" that is clean:

const client = new MkDB.MkDBClient({
    host: '127.0.0.1',
    port: 80, // Browser defaults to HTTP port
    transport: 'http'
});

async function run() {
    await client.connect();
    const stores = await client.listStores();
    console.log(stores);
}
