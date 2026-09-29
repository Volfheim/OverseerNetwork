const { test } = require('node:test');
const assert = require('node:assert/strict');
const { position, groupSites, arcPoints, placeLabels } = require('../static/js/map_geometry.js');

const near = (a, b) => assert.ok(Math.abs(a - b) < 0.00001, `${a} != ${b}`);

test('coordinates align with the equirectangular Earth texture', () => {
    const greenwich = position([0, 0], 100);
    near(greenwich[0], 100); near(greenwich[1], 0); near(greenwich[2], 0);
    near(position([0, 90], 100)[2], -100);
    near(position([90, 0], 100)[1], 100);
});

test('co-located nodes share one site without changing inventory', () => {
    const nodes = [
        { id: 'hq', coordinates: [55.7558, 37.6173] },
        { id: 'msk', coordinates: [55.7558, 37.6173] },
        { id: 'ny', coordinates: [40.7128, -74.006] },
    ];
    const copy = JSON.stringify(nodes);
    const sites = groupSites(nodes);
    assert.deepEqual(sites.map(site => site.nodes.map(node => node.id)), [['hq', 'msk'], ['ny']]);
    assert.equal(JSON.stringify(nodes), copy);
});

test('invalid coordinates are not silently placed in the Gulf of Guinea', () => {
    const sites = groupSites([
        { id: 'missing' }, { id: 'invalid', coordinates: [NaN, 0] },
        { id: 'outside', coordinates: [91, 0] }, { id: 'null', coordinates: [null, 0] },
        { id: 'real-origin', coordinates: [0, 0] },
    ]);
    assert.deepEqual(sites.map(site => site.nodes[0].id), ['real-origin']);
});

test('NY to Moscow route stays above the surface along its entire length', () => {
    const points = arcPoints([40.7128, -74.006], [55.7558, 37.6173]);
    assert.ok(points.length > 30);
    for (const point of points) assert.ok(Math.hypot(...point) >= 102.99);
    near(Math.hypot(...points[0]), 103);
    near(Math.hypot(...points.at(-1)), 103);
    assert.ok(Math.hypot(...points[Math.floor(points.length / 2)]) > 115);
});

test('antipodal and coincident routes remain finite and above ground', () => {
    for (const end of [[0, 180], [0, 0], [0, 179.99999]]) {
        for (const point of arcPoints([0, 0], end)) {
            assert.ok(point.every(Number.isFinite));
            assert.ok(Math.hypot(...point) >= 102.99);
        }
    }
});

test('date-line route crosses the Pacific, not Greenwich', () => {
    const points = arcPoints([0, 170], [0, -170]);
    assert.ok(points.every(point => point[0] < -100));
});

test('labels avoid collisions and stay inside a mobile map', () => {
    const labels = [0, 1, 2].map(id => ({ id, x: 185, y: 70, width: 130, height: 42 }));
    const rects = placeLabels(labels, { width: 390, height: 280 });
    assert.equal(rects.length, 3);
    for (const a of rects) {
        assert.ok(a.x >= 8 && a.y >= 8 && a.x + a.width <= 382 && a.y + a.height <= 272);
        for (const b of rects) {
            if (a === b) continue;
            assert.ok(a.x + a.width <= b.x || b.x + b.width <= a.x || a.y + a.height <= b.y || b.y + b.height <= a.y);
        }
    }
});

test('labels that cannot fit are omitted instead of overlapping controls', () => {
    assert.deepEqual(placeLabels([{ id: 'wide', x: 20, y: 20, width: 300, height: 50 }], { width: 200, height: 100 }), []);
});

test('labels do not cover the desktop map toolbar', () => {
    const rects = placeLabels([{ id: 'edge', x: 100, y: 70, width: 130, height: 42 }],
        { width: 800, height: 500 }, [{ x: 20, y: 20, width: 40, height: 350 }]);
    assert.equal(rects.length, 1);
    assert.ok(rects[0].x >= 66);
});
