import { MkDBClient, Q, MkDBController } from './src';

async function main() {
    // --- Data Plane ---
    const client = new MkDBClient({
        host: '127.0.0.1',
        transport: 'socket',
        trackRecords: true
    });

    await client.connect();
    console.log('Data plane connected');

    // --- Control Plane ---
    const ctrl = new MkDBController('127.0.0.1', 8090);
    try {
        const role = await ctrl.login('admin', 'password123');
        console.log('Logged into control plane as:', role);

        const dbStats = await ctrl.dashboard();
        console.log('DB Stores count:', Object.keys(dbStats.stores).length);

        // Create a new store via control plane
        await ctrl.createStore('new_test_store', { description: 'Created via TS' });
        console.log('Store created');
    } catch (e) {
        console.error('Control plane error:', e.message);
    }

    // ... rest of the data plane example
}

// 3. Write data
await client.set('products', 'p1', {
    name: 'Awesome Laptop',
    specs: { ram: '16GB', cpu: 'i7' },
    price: 1200
});

// 4. Read data with tracking
const resp = await client.get('products', 'p1');
if (resp.found) {
    const product = resp.data;
    console.log('Got product:', product.name);

    // Modify nested field and patch
    product.specs.ram = '32GB';
    product.price = 1150;

    await product.patch();
    console.log('Product patched!');
}

// 5. Query
const results = await client.query('products', Q.where('price', '<=', 1200).build());
console.log('Query results:', results.ids);
}

main().catch(console.error);
