(function (root) {
    'use strict';
    function applyLocations(nodes, locations) {
        let moved = false;
        nodes.forEach(node => {
            const observation = locations[node.id];
            if (!observation) return;
            const c = observation.coordinates;
            const valid = ['ready', 'stale'].includes(observation.status) && Array.isArray(c) &&
                c.length === 2 && c.every(Number.isFinite) && Math.abs(c[0]) <= 90 && Math.abs(c[1]) <= 180;
            const coordinates = valid ? [...c] : null;
            moved ||= JSON.stringify(node.coordinates) !== JSON.stringify(coordinates);
            node.geo = { ...observation, coordinates };
            node.coordinates = coordinates;
            for (const field of ['city', 'country', 'provider']) node[field] = valid ? observation[field] || null : null;
        });
        return moved;
    }
    if (typeof module !== 'undefined' && module.exports) { module.exports = { applyLocations }; return; }
    let busy = false;
    async function refresh(refreshNow = false) {
        if (busy) return;
        busy = true;
        const button = document.getElementById('refresh-geo-btn');
        const feedback = document.getElementById('geo-feedback');
        if (button) button.disabled = true;
        if (feedback) feedback.textContent = root.t('geoScan');
        try {
            const response = await fetch('/api/geo/nodes' + (refreshNow ? '?refresh=true' : ''), { signal: AbortSignal.timeout(20000) });
            if (!response.ok) throw new Error('Geolocation unavailable');
            const data = await response.json();
            const moved = applyLocations(root.OVERSEER_SERVERS, data.locations);
            root.dispatchEvent(new CustomEvent('overseer:geo', { detail: { moved } }));
            root.renderOverseerControlPanel?.();
            const ready = Object.values(data.locations).filter(item => item.status === 'ready').length;
            if (feedback) feedback.textContent = root.t('geoResult', { ready, total: root.OVERSEER_SERVERS.length });
        } catch {
            root.OVERSEER_SERVERS.forEach(node => {
                if (node.geo?.source === 'physical') {
                    const network = node.geo.network || {};
                    node.geo = { ...node.geo, network: { ...network, status: network.ip ? 'stale' : 'unavailable' } };
                } else if (node.auto_geo) node.geo = { ...node.geo, status: node.coordinates ? 'stale' : 'unavailable' };
            });
            root.dispatchEvent(new CustomEvent('overseer:geo', { detail: { moved: false } }));
            root.renderOverseerControlPanel?.();
            if (feedback) feedback.textContent = root.t('geoFailed');
        } finally {
            busy = false;
            if (button) button.disabled = false;
        }
    }
    root.refreshOverseerGeo = refresh;
    document.addEventListener('DOMContentLoaded', () => {
        document.getElementById('refresh-geo-btn')?.addEventListener('click', () => refresh(true));
        refresh();
        setInterval(() => { if (!document.hidden) refresh(); }, 60000);
    }, { once: true });
    document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
})(typeof window === 'undefined' ? globalThis : window);
