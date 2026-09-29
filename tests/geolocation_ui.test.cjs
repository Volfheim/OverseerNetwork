const { test } = require('node:test');
const assert = require('node:assert/strict');
const { applyLocations } = require('../static/js/geolocation.js');
test('IP observations update map coordinates without rewriting the SSH destination', () => {
    const nodes = [{ id: 'pc', type: 'local', coordinates: null },
        { id: 'vpn', type: 'ssh', host: '192.0.2.10', coordinates: [0, 0] }];
    const moved = applyLocations(nodes, {
        pc: { status: 'ready', source: 'physical', network: { ip: '8.8.8.8', coordinates: [40.7, -74] }, city: 'London', coordinates: [51.5, -0.12] },
        vpn: { status: 'ready', source: 'server-ip', ip: '192.0.2.10', coordinates: [40.7, -74] },
    });
    assert.equal(moved, true);
    assert.deepEqual(nodes[0].coordinates, [51.5, -0.12]);
    assert.equal(nodes[0].city, 'London');
    assert.equal(nodes[1].host, '192.0.2.10');
    assert.equal(nodes[0].host, undefined);
    assert.equal(nodes[0].geo.network.ip, '8.8.8.8');
});

test('a VPN IP refresh cannot move the physical PC marker', () => {
    const nodes = [{ id: 'pc', type: 'local', physical_location: [51.5, -0.12], coordinates: [51.5, -0.12] }];
    const moved = applyLocations(nodes, { pc: { status: 'ready', source: 'physical', coordinates: [51.5, -0.12],
        network: { ip: '1.1.1.1', coordinates: [-33, 151] } } });
    assert.equal(moved, false);
    assert.deepEqual(nodes[0].coordinates, [51.5, -0.12]);
    assert.deepEqual(nodes[0].physical_location, [51.5, -0.12]);
});
test('failed geolocation removes a false Moscow marker instead of retaining it', () => {
    const nodes = [{ id: 'pc', coordinates: [55.7, 37.6], city: 'Moscow' }];
    applyLocations(nodes, { pc: { status: 'unavailable', coordinates: null } });
    assert.equal(nodes[0].coordinates, null);
    assert.equal(nodes[0].city, null);
});
test('health or timestamp refresh does not rebuild unchanged map geometry', () => {
    const nodes = [{ id: 'ny', coordinates: [40.7, -74] }];
    assert.equal(applyLocations(nodes, { ny: { status: 'ready', coordinates: [40.7, -74], checked_at: 'new' } }), false);
    assert.equal(nodes[0].geo.checked_at, 'new');
});
