(function (root) {
    'use strict';
    const radians = Math.PI / 180;
    const normalize = v => {
        const length = Math.hypot(...v);
        return v.map(value => value / length);
    };

    function position([lat, lon], radius = 1) {
        const latitude = lat * radians;
        const longitude = lon * radians;
        return [radius * Math.cos(latitude) * Math.cos(longitude),
            radius * Math.sin(latitude), -radius * Math.cos(latitude) * Math.sin(longitude)];
    }

    function groupSites(nodes) {
        const groups = new Map();
        nodes.forEach(node => {
            const coords = node.coordinates;
            if (!Array.isArray(coords) || coords.length !== 2 || !coords.every(Number.isFinite) ||
                Math.abs(coords[0]) > 90 || Math.abs(coords[1]) > 180) return;
            const key = coords.map(value => value.toFixed(4)).join(',');
            if (!groups.has(key)) groups.set(key, { key, coordinates: [...coords], nodes: [] });
            groups.get(key).nodes.push(node);
        });
        return [...groups.values()];
    }

    function arcPoints(origin, destination, segments = 72) {
        const a = position(origin);
        const b = position(destination);
        const dot = Math.max(-1, Math.min(1, a.reduce((sum, value, i) => sum + value * b[i], 0)));
        const angle = Math.acos(dot);
        let tangent = b.map((value, i) => value - a[i] * dot);
        // Antipodes have no unique great circle. Choose a stable perpendicular.
        if (Math.hypot(...tangent) < 0.000001) {
            tangent = Math.abs(a[1]) < 0.9 ? [-a[2], 0, a[0]] : [0, a[2], -a[1]];
        }
        tangent = normalize(tangent);
        const lift = Math.min(27, angle * 18);
        return Array.from({ length: segments + 1 }, (_, index) => {
            const t = index / segments;
            const radius = 103 + Math.sin(Math.PI * t) * lift;
            return a.map((value, i) => (value * Math.cos(angle * t) + tangent[i] * Math.sin(angle * t)) * radius);
        });
    }

    function placeLabels(labels, bounds, obstacles = []) {
        const placed = [];
        const pad = 8;
        labels.forEach(label => {
            if (label.width > bounds.width - pad * 2 || label.height > bounds.height - pad * 2) return;
            const right = label.x + 15;
            const left = label.x - label.width - 15;
            const desiredX = label.x < bounds.width / 2 ? left : right;
            const alternateX = label.x < bounds.width / 2 ? right : left;
            const desiredY = label.y - label.height - 14;
            const clampX = x => Math.max(pad, Math.min(bounds.width - label.width - pad, x));
            const clampY = y => Math.max(pad, Math.min(bounds.height - label.height - pad, y));
            const candidates = [];
            for (let row = 0; row <= labels.length; row++) {
                for (const side of [1, -1]) {
                    candidates.push({ x: clampX(desiredX), y: clampY(desiredY + side * row * (label.height + 8)) });
                    candidates.push({ x: clampX(alternateX), y: clampY(desiredY + side * row * (label.height + 8)) });
                }
            }
            const chosen = candidates.find(candidate => ![...placed, ...obstacles].some(rect =>
                candidate.x < rect.x + rect.width + 6 && candidate.x + label.width + 6 > rect.x &&
                candidate.y < rect.y + rect.height + 6 && candidate.y + label.height + 6 > rect.y));
            if (chosen) placed.push({ ...label, ...chosen });
        });
        return placed;
    }

    const api = { position, groupSites, arcPoints, placeLabels };
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    else root.OverseerMapGeometry = api;
})(typeof window === 'undefined' ? globalThis : window);
