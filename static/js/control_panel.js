const state = {
    servers: window.OVERSEER_SERVERS || [],
    statuses: window.OVERSEER_STATUSES || {},
    selectedId: (window.OVERSEER_SERVERS || [])[0]?.id || null,
};
let editorBusy = false;

function markEditorDirty(dirty = true) {
    document.getElementById('server-form').dataset.dirty = String(dirty);
    renderEditorHeading();
}

function renderEditorHeading() {
    const saved = state.servers.find(server => server.id === formField('id').value);
    document.getElementById('editor-title').textContent = formField('id').disabled ? saved?.name || t('editNode') : t('addNode');
    document.getElementById('draft-state').textContent = document.getElementById('server-form').dataset.dirty === 'true' ? t('unsavedChanges') : '';
}

async function mayReplaceDraft() {
    if (editorBusy) return false;
    return document.getElementById('server-form').dataset.dirty !== 'true' || await window.overseerConfirm(t('discardDraft'));
}

async function openEditor(server = null, type = 'ssh') {
    const sameDraft = server && formField('id').disabled && formField('id').value === server.id;
    if (!sameDraft && !await mayReplaceDraft()) return;
    if (!sameDraft) {
        fillForm(server);
        if (!server) {
            formField('type').value = type;
            if (type === 'local') formField('name').value = t('localPcName');
            updateGeoMode();
        }
    }
    state.selectedId = server?.id || null;
    renderOverseerControlPanel();
    switchPanel('editor');
    formField('name').focus();
}

function revealInvalidField(field) {
    for (let parent = field.parentElement; parent; parent = parent.parentElement) {
        if (parent.tagName === 'DETAILS') parent.open = true;
    }
    field.focus();
}

function apiErrorMessage(detail, fallback = 'saveFailed') {
    if (Array.isArray(detail)) return detail.map(item => item.msg || t(fallback)).join('; ');
    return typeof detail === 'string' ? t(detail) : t(fallback);
}

function saveRequest(payload, editing, servers = state.servers) {
    // A new draft must never turn into an update just because its ID already exists.
    if (!editing && servers.some(server => server.id === payload.id)) throw new Error(t('duplicateNodeId'));
    return {url: editing ? `/api/servers/${encodeURIComponent(payload.id)}` : '/api/servers', method: editing ? 'PUT' : 'POST'};
}

function setEditorBusy(busy) {
    editorBusy = busy;
    document.getElementById('server-form').inert = busy;
    for (const id of ['save-node-btn', 'cancel-node-btn', 'delete-node-btn']) document.getElementById(id).disabled = busy;
}

function showSavedNode(id) {
    try { sessionStorage.setItem('overseer.savedNode', id); } catch { /* Storage may be disabled. */ }
    markEditorDirty(false);
    window.location.reload();
}

function escapeHtml(value) {
    return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#039;');
}

function formField(name) {
    return document.getElementById('server-form').elements[name];
}

function currentServer() {
    return state.servers.find(server => server.id === state.selectedId) || state.servers[0] || null;
}

function currentStatus(serverId) {
    return state.statuses[serverId] || null;
}

function statusLabel(serverId) {
    const status = currentStatus(serverId)?.status;
    if (status === 'ONLINE') return t('online');
    if (status === 'OFFLINE') return t('offline');
    return t('unknown');
}

function statusClass(serverId) {
    return (currentStatus(serverId)?.status || 'UNKNOWN').toLowerCase();
}

function serverLocation(server) {
    return [server?.city, server?.country].filter(Boolean).join(', ') || server?.coordinates?.map(v => v.toFixed(2)).join(', ') || t('noLocation');
}

function geoDescription(server) {
    const geo = server?.geo;
    if (!geo) return '';
    if (geo.status === 'needs-location') return t('geoNeedsLocation');
    if (geo.status === 'pending') return t('geoScan');
    if (geo.status === 'unavailable') return t('geoFailed');
    if (geo.source === 'physical') {
        const network = geo.network;
        return [t('geoPhysical'), network?.ip ? `${t('geoPcEgress')}: ${network.ip} (${network.city || '?'})` : '',
            network?.status === 'stale' ? t('geoStale') : ''].filter(Boolean).join(' / ');
    }
    const source = geo.source === 'server-ip' ? t('geoIpApprox') : t('mapRegistryCoords');
    const time = geo.checked_at ? new Date(geo.checked_at).toLocaleTimeString(overseerLang(), { hour: '2-digit', minute: '2-digit' }) : '';
    return [source, geo.ip, time, geo.status === 'stale' ? t('geoStale') : ''].filter(Boolean).join(' / ');
}

function setFormStatus(message, isError = false) {
    const el = document.getElementById('form-status');
    el.textContent = message || '';
    el.classList.toggle('error', isError);
}

function setSettingsStatus(message, isError = false) {
    const el = document.getElementById('settings-status');
    if (!el) return;
    el.textContent = message || '';
    el.classList.toggle('error', isError);
}

function switchPanel(name) {
    document.body.classList.toggle('panel-focused', ['editor', 'settings', 'services'].includes(name));
    document.querySelectorAll('[data-deck-tab]').forEach(button => {
        button.classList.toggle('active', button.dataset.deckTab === name);
    });
    document.querySelectorAll('[data-deck-panel]').forEach(panel => {
        panel.classList.toggle('active', panel.dataset.deckPanel === name);
    });
}

function renderOverview() {
    const statuses = Object.values(state.statuses);
    const total = state.servers.length;
    const online = statuses.filter(status => status.status === 'ONLINE').length;
    const offline = statuses.filter(status => status.status === 'OFFLINE').length;
    const local = state.servers.filter(server => server.type === 'local').length;
    document.getElementById('overview-summary').innerHTML = `
        <div><span>${t('nodes')}</span><strong>${total}</strong></div>
        <div><span>${t('online')}</span><strong>${online}</strong></div>
        <div><span>${t('offline')}</span><strong>${offline}</strong></div>
        <div><span>${t('localFeed')}</span><strong>${local}</strong></div>
    `;
}

function renderSelectedNode() {
    const server = currentServer();
    const panel = document.getElementById('selected-node-panel');
    if (!server) {
        panel.innerHTML = `
            <strong>${t('addFirstNode')}</strong>
            <span>${t('nodeFocusHint')}</span>
        `;
        return;
    }

    const status = currentStatus(server.id);
    const latency = status?.latency_ms;
    const latencyText = latency || latency === 0 ? `${latency} ms` : '--';
    panel.innerHTML = `
        <div>
            <span>${escapeHtml(server.role || server.type)} /// ${escapeHtml(serverLocation(server))}</span>
            <strong>${escapeHtml(server.name)}</strong>
            <small>${escapeHtml(server.host || 'LOCAL')} : ${escapeHtml(server.port || 'HQ')} /// ${latencyText}</small>
            <small class="geo-context">${escapeHtml(geoDescription(server))}</small>
        </div>
        <div class="node-card-status ${statusClass(server.id)}">${escapeHtml(statusLabel(server.id))}</div>
        <div class="selected-actions">
            <button data-selected-action="terminal">${t('terminal')}</button>
            <button data-selected-action="focus">${t('focus')}</button>
            <button data-selected-action="edit">${t('edit')}</button>
        </div>
    `;
}

function updateMapIntel() {
    const server = currentServer();
    const title = document.getElementById('intel-title');
    const body = document.getElementById('intel-body');
    if (!server) {
        title.textContent = t('noNode');
        body.textContent = t('nodeFocusHint');
        return;
    }
    const status = statusLabel(server.id);
    const statusData = currentStatus(server.id);
    const latency = statusData?.latency_ms;
    const latencyText = latency || latency === 0 ? `${latency} ms` : '--';
    title.textContent = server.name;
    body.textContent = `${status} /// ${serverLocation(server)} /// ${server.geo?.ip || server.host || 'LOCAL'} /// ${latencyText}`;
}

function renderNodeList() {
    const list = document.getElementById('node-list');
    if (!state.servers.length) {
        list.innerHTML = `<div class="empty-state">${t('addFirstNode')}</div>`;
        return;
    }
    const active = list.contains(document.activeElement) ? document.activeElement : null;
    const markup = state.servers.map(server => {
        const latency = currentStatus(server.id)?.latency_ms;
        const latencyText = latency || latency === 0 ? `${latency} ms` : '--';
        return `
            <article class="node-card ${state.selectedId === server.id ? 'selected' : ''}" data-node-id="${escapeHtml(server.id)}">
                <div>
                    <strong>${escapeHtml(server.name)}</strong>
                    <span>${escapeHtml(server.role || server.type)} /// ${escapeHtml(serverLocation(server))}</span>
                </div>
                <div class="node-card-status ${statusClass(server.id)}">${escapeHtml(statusLabel(server.id))}</div>
                <div class="node-card-meta">${escapeHtml(server.host || 'LOCAL')} : ${escapeHtml(server.port || 'HQ')} /// ${latencyText}</div>
                <div class="node-actions">
                    <button data-action="focus" data-node-id="${escapeHtml(server.id)}">${t('focus')}</button>
                    <button data-action="terminal" data-node-id="${escapeHtml(server.id)}">${t('terminal')}</button>
                    <button data-action="edit" data-node-id="${escapeHtml(server.id)}">${t('edit')}</button>
                </div>
            </article>
        `;
    }).join('');
    if (list.innerHTML !== markup) {
        const action = active?.dataset.action, id = active?.dataset.nodeId;
        list.innerHTML = markup;
        if (action && id) [...list.querySelectorAll('button[data-action]')]
            .find(button => button.dataset.action === action && button.dataset.nodeId === id)?.focus({preventScroll: true});
    }
}

function renderLocalMetrics() {
    const metricsBox = document.getElementById('local-metrics');
    const localServer = state.servers.find(server => server.type === 'local');
    const metrics = localServer ? currentStatus(localServer.id)?.metrics : null;
    if (!metrics) {
        metricsBox.innerHTML = `<span>${t('localFeed')}</span><strong>${t('waiting')}</strong>`;
        return;
    }
    metricsBox.innerHTML = `
        <span>${t('cpu')}</span><strong>${Number(metrics.cpu_percent).toFixed(1)}%</strong>
        <span>${t('ram')}</span><strong>${Number(metrics.ram_percent).toFixed(1)}%</strong>
        <span>${t('disk')}</span><strong>${Number(metrics.disk_percent).toFixed(1)}%</strong>
        <span>${t('host')}</span><strong>${escapeHtml(metrics.hostname)}</strong>
    `;
}

function renderNetworkState() {
    const statuses = Object.values(state.statuses);
    const online = statuses.filter(status => status.status === 'ONLINE').length;
    const total = state.servers.length;
    const label = total ? `${online}/${total} ${t('online')}` : t('addFirstNode');
    document.getElementById('network-state').textContent = label;
}

function renderAccess() {
    const access = window.OVERSEER_ACCESS || {};
    const accessUrl = document.getElementById('access-url');
    const tokenInput = document.getElementById('access-token');
    if (accessUrl) accessUrl.value = access.local_url || '';
    if (tokenInput) {
        tokenInput.type = 'password';
        tokenInput.value = access.token || '';
    }
}

function renderOverseerControlPanel() {
    state.statuses = window.OVERSEER_STATUSES || state.statuses || {};
    window.applyOverseerLanguage?.();
    renderNetworkState();
    renderOverview();
    renderSelectedNode();
    renderNodeList();
    renderLocalMetrics();
    renderAccess();
    updateMapIntel();
    window.initOverseerPrefControls?.(document);
    window.updateOverseerMapLabels?.();
    renderEditorHeading();
}

window.renderOverseerControlPanel = renderOverseerControlPanel;

function fillForm(server) {
    document.getElementById('server-form').dataset.dirty = 'false';
    formField('id').value = server?.id || 'node_' + crypto.randomUUID().slice(0, 8);
    formField('id').disabled = Boolean(server?.id);
    formField('type').value = server?.type || 'ssh';
    formField('profile').value = server?.profile || 'generic';
    formField('environment').value = server?.environment || 'personal';
    formField('control_mode').value = server?.control_mode || 'observe';
    formField('protected').checked = !!server?.protected;
    formField('protected_targets').value = (server?.protected_targets || []).join('\n');
    formField('clear_key').checked = false;
    document.getElementById('clear-key-row').hidden = !server?.has_key;
    formField('upstream_id').innerHTML = `<option value="">${t('noUpstream')}</option>` + state.servers
        .filter(item => item.id !== server?.id)
        .map(item => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}</option>`).join('');
    formField('upstream_id').value = server?.upstream_id || '';
    document.getElementById('custom-services').innerHTML = '';
    (server?.services || []).forEach(addServiceRow);
    document.getElementById('delete-node-btn').disabled = !server;
    document.getElementById('delete-node-btn').hidden = !server;
    document.querySelectorAll('#server-form details').forEach(details => { details.open = false; });
    formField('name').value = server?.name || '';
    formField('role').value = server?.role || 'node';
    formField('tags').value = (server?.tags || []).join(', ');
    formField('host').value = server?.host || '';
    formField('port').value = server?.port || 22;
    formField('username').value = server?.username || '';
    formField('key_path').value = '';
    const coordinates = server?.type === 'local' ? server.physical_location : server?.coordinates;
    formField('lat').value = coordinates?.[0] ?? '';
    formField('lon').value = coordinates?.[1] ?? '';
    formField('city').value = server?.city || '';
    formField('country').value = server?.country || '';
    formField('provider').value = server?.provider || '';
    formField('notes').value = server?.notes || '';
    formField('auto_geo').checked = server?.auto_geo ?? true;
    formField('strict_host_key').checked = server?.strict_host_key ?? true;
    updateGeoMode();
    window.overseerSetup?.fill(server);
    setFormStatus('');
    renderEditorHeading();
    document.getElementById('server-form').scrollTop = 0;
}

function payloadFromForm() {
    const payload = {
        id: formField('id').value.trim(),
        type: formField('type').value,
        profile: formField('profile').value,
        environment: formField('environment').value,
        control_mode: formField('control_mode').value,
        protected: formField('protected').checked,
        protected_targets: formField('protected_targets').value.split(/[\n,]+/).map(v => v.trim()).filter(Boolean),
        upstream_id: formField('upstream_id').value || null,
        services: [...document.querySelectorAll('.custom-service')].map(row => ({
            id: row.dataset.serviceId,
            label: row.querySelector('[data-service="label"]').value.trim(),
            kind: row.querySelector('[data-service="kind"]').value,
            target: row.querySelector('[data-service="target"]').value.trim(),
            required: row.querySelector('[data-service="required"]').checked,
            allow_restart: row.querySelector('[data-service="allow_restart"]').checked,
        })),
        name: formField('name').value.trim(),
        role: formField('role').value.trim() || 'node',
        tags: formField('tags').value.split(',').map(tag => tag.trim()).filter(Boolean),
        host: formField('host').value.trim() || null,
        port: Number(formField('port').value || 22),
        username: formField('username').value.trim() || null,
        key_path: formField('key_path').value.trim() || null,
        coordinates: [Number(formField('lat').value || 0), Number(formField('lon').value || 0)],
        city: formField('city').value.trim() || null,
        country: formField('country').value.trim() || null,
        provider: formField('provider').value.trim() || null,
        notes: formField('notes').value.trim() || null,
        auto_geo: formField('auto_geo').checked,
        strict_host_key: formField('strict_host_key').checked,
        host_key: window.overseerSetup?.hostKey() || null,
    };
    if (payload.type === 'local') {
        payload.physical_location = formField('lat').value !== '' && formField('lon').value !== '' ? payload.coordinates : null;
        payload.host = null;
        payload.username = null;
        payload.key_path = null;
        payload.host_key = null;
        payload.port = 22;
    }
    return payload;
}

async function saveServer(event) {
    event.preventDefault();
    if (editorBusy) return;
    if (formField('type').value === 'ssh' && !window.overseerSetup?.validConnection()) return;
    if (!formField('name').value.trim()) { setFormStatus(t('nodeNameRequired'), true); formField('name').focus(); return; }
    if (formField('type').value === 'ssh' && window.overseerSetup?.pendingTrust()) {
        setFormStatus(t('trustRequired'), true);
        return;
    }
    const payload = payloadFromForm();
    if (!payload.id) {
        setFormStatus(t('nodeIdRequired'), true);
        return;
    }
    const existing = formField('id').disabled;
    if (existing) payload.clear_key = formField('clear_key').checked;
    try {
    const {url, method} = saveRequest(payload, existing);
    setEditorBusy(true);
    setFormStatus(t('transmitting'));
    const response = await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        signal: AbortSignal.timeout(20000),
    });
    if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        setFormStatus(apiErrorMessage(error.detail), true);
        return;
    }
    showSavedNode(payload.id);
    } catch (error) { setFormStatus(error.name === 'Error' ? error.message : t('saveUncertain'), true); }
    finally { setEditorBusy(false); }
}

async function deleteServer() {
    if (editorBusy) return;
    const server = state.servers.find(item => item.id === formField('id').value);
    if (!server) return;
    if (!await window.overseerConfirm(t('removeNodeConfirm', {name: server.name}))) return;
    try {
    setEditorBusy(true);
    setFormStatus(t('decommissioning'));
    const response = await fetch(`/api/servers/${encodeURIComponent(server.id)}`, { method: 'DELETE', signal: AbortSignal.timeout(20000) });
    if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        setFormStatus(apiErrorMessage(error.detail, 'deleteFailed'), true);
        return;
    }
    markEditorDirty(false);
    window.location.reload();
    } catch { setFormStatus(t('saveUncertain'), true); }
    finally { setEditorBusy(false); }
}

async function refreshStatus() {
    await window.refreshOverseerGeo?.(true);
    await window.refreshOverseerFleet?.();
    setSettingsStatus(t('polling'));
    const response = await fetch('/api/status');
    if (!response.ok) {
        setSettingsStatus(t('statusFailed'), true);
        return;
    }
    window.OVERSEER_STATUSES = await response.json();
    if (window.applyOverseerStatuses) window.applyOverseerStatuses(window.OVERSEER_STATUSES);
    renderOverseerControlPanel();
    setSettingsStatus(t('statusUpdated'));
}

async function reloadConfig() {
    if (!await mayReplaceDraft()) return;
    setSettingsStatus(t('registryReloading'));
    const response = await fetch('/api/config/reload', { method: 'POST' });
    if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        setSettingsStatus(error.detail || t('reloadFailed'), true);
        return;
    }
    setSettingsStatus(t('registryReloaded'));
    markEditorDirty(false);
    setTimeout(() => window.location.reload(), 300);
}

async function exportRegistry() {
    try {
    const response = await fetch('/api/setup/export');
    if (!response.ok) throw new Error();
    const payload = await response.json();
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    const link = document.createElement('a');
    const url = URL.createObjectURL(blob);
    link.href = url;
    link.download = `overseer-registry-${new Date().toISOString().slice(0, 10)}.json`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    setSettingsStatus(t('registryExportReady'));
    } catch { setSettingsStatus(t('connectionFailed'), true); }
}

async function autoGeo() {
    const host = formField('host').value.trim();
    if (formField('type').value === 'ssh' && !host) { setFormStatus(t('geoHostRequired'), true); return; }
    const nodeId = formField('id').value;
    const button = document.getElementById('geo-node-btn');
    button.disabled = true;
    try {
    setFormStatus(t('geoScan'));
    const response = await fetch('/api/geo/lookup', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ host }),
        signal: AbortSignal.timeout(20000),
    });
    if (formField('id').value !== nodeId || formField('host').value.trim() !== host || formField('type').value !== 'ssh') return;
    if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        setFormStatus(error.detail || t('geoFailed'), true);
        return;
    }
    const geo = await response.json();
    formField('lat').value = geo.coordinates[0];
    formField('lon').value = geo.coordinates[1];
    formField('city').value = geo.city || '';
    formField('country').value = geo.country || '';
    formField('provider').value = geo.provider || '';
    markEditorDirty();
    setFormStatus(t('geoLocked'));
    } catch { if (formField('id').value === nodeId) setFormStatus(t('geoFailed'), true); }
    finally { button.disabled = false; }
}

function updateGeoMode() {
    const local = formField('type').value === 'local';
    document.getElementById('connection-fields').hidden = local;
    document.querySelectorAll('#connection-fields input').forEach(input => { input.disabled = local; });
    formField('host').required = formField('username').required = !local;
    document.querySelector('.geo-mode').hidden = local;
    document.getElementById('geo-node-btn').hidden = local;
    document.getElementById('device-geo-btn').hidden = !local;
    document.getElementById('location-fields').hidden = !local && formField('auto_geo').checked;
    document.querySelectorAll('#location-fields input').forEach(input => { input.disabled = !local && formField('auto_geo').checked; });
    formField('lat').required = formField('lon').required = local
        ? Boolean(formField('lat').value || formField('lon').value)
        : !formField('auto_geo').checked;
}

async function deviceGeo() {
    if (!navigator.geolocation) { setFormStatus(t('deviceGeoUnavailable'), true); return; }
    if (!await window.overseerConfirm(t('deviceGeoConfirm'))) return;
    const nodeId = formField('id').value;
    setFormStatus(t('geoScan'));
    navigator.geolocation.getCurrentPosition(position => {
        if (formField('id').value !== nodeId || formField('type').value !== 'local') return;
        formField('lat').value = position.coords.latitude.toFixed(4);
        formField('lon').value = position.coords.longitude.toFixed(4);
        formField('city').value = '';
        formField('country').value = '';
        markEditorDirty();
        setFormStatus(t('deviceGeoReady'));
    }, () => setFormStatus(t('deviceGeoUnavailable'), true), { enableHighAccuracy: false, timeout: 15000, maximumAge: 60000 });
}

function selectServer(serverId, focusMap = true) {
    const server = state.servers.find(item => item.id === serverId);
    if (!server) return;
    state.selectedId = serverId;
    if (focusMap && window.focusOverseerNode) window.focusOverseerNode(server.id);
    renderOverseerControlPanel();
    window.dispatchEvent(new Event('overseer:selection'));
}

function addServiceRow(service = {}) {
    const row = document.createElement('div');
    row.className = 'custom-service';
    row.dataset.serviceId = service.id || 'svc_' + crypto.randomUUID().slice(0, 8);
    row.innerHTML = `
        <label><span data-i18n="name">${t('name')}</span><input data-service="label" required maxlength="100" value="${escapeHtml(service.label || '')}"></label>
        <label><span data-i18n="type">${t('type')}</span><select data-service="kind"><option value="systemd">systemd</option><option value="container">Docker</option></select></label>
        <label class="service-target"><span data-i18n="serviceTarget">${t('serviceTarget')}</span><input data-service="target" required pattern="[a-zA-Z0-9][a-zA-Z0-9_.@-]{0,127}" value="${escapeHtml(service.target || '')}"></label>
        <label><input type="checkbox" data-service="required" ${service.required !== false ? 'checked' : ''}> <span data-i18n="requiredService">${t('requiredService')}</span></label>
        <label><input type="checkbox" data-service="allow_restart" ${service.allow_restart ? 'checked' : ''}> <span data-i18n="allowRestart">${t('allowRestart')}</span></label>
        <button type="button" data-remove-service data-i18n-title="delete" data-i18n-aria="delete" title="${t('delete')}" aria-label="${t('delete')}"><span class="map-icon icon-close" aria-hidden="true"></span></button>`;
    row.querySelector('[data-service="kind"]').value = service.kind || 'systemd';
    row.querySelector('[data-remove-service]').addEventListener('click', () => { row.remove(); markEditorDirty(); });
    document.getElementById('custom-services').appendChild(row);
}

window.selectOverseerServer = selectServer;

function openSelectedTerminal() {
    const server = currentServer();
    if (!server) return;
    document.getElementById('terminal-modal').classList.remove('hidden');
    window.openTerminal(server.id, server.name);
}

function initDeckEvents() {
    document.querySelectorAll('[data-deck-tab]').forEach(button => {
        button.addEventListener('click', () => {
            if (button.dataset.deckTab === 'editor' && formField('id').disabled && document.getElementById('server-form').dataset.dirty !== 'true') {
                fillForm(currentServer());
            }
            switchPanel(button.dataset.deckTab);
        });
    });

    document.getElementById('node-list').addEventListener('click', event => {
        const button = event.target.closest('button[data-action]');
        const card = event.target.closest('[data-node-id]');
        const id = button?.dataset.nodeId || card?.dataset.nodeId;
        if (!id) return;
        const server = state.servers.find(item => item.id === id);
        if (!server) return;
        if (button?.dataset.action === 'edit') { openEditor(server); return; }
        state.selectedId = id;
        if (button?.dataset.action === 'terminal') {
            openSelectedTerminal();
        } else {
            if (window.focusOverseerNode) window.focusOverseerNode(server.id);
        }
        renderOverseerControlPanel();
        window.dispatchEvent(new Event('overseer:selection'));
    });

    document.getElementById('selected-node-panel').addEventListener('click', event => {
        const button = event.target.closest('[data-selected-action]');
        if (!button) return;
        const action = button.dataset.selectedAction;
        const server = currentServer();
        if (!server) return;
        if (action === 'terminal') openSelectedTerminal();
        if (action === 'focus' && window.focusOverseerNode) window.focusOverseerNode(server.id);
        if (action === 'edit') {
            openEditor(server);
        }
    });

    document.getElementById('server-form').addEventListener('submit', saveServer);
    document.getElementById('server-form').addEventListener('input', event => {
        if (!['template-name', 'server-template'].includes(event.target.id)) markEditorDirty();
    });
    document.getElementById('server-form').addEventListener('invalid', event => revealInvalidField(event.target), true);
    window.addEventListener('beforeunload', event => {
        if (document.getElementById('server-form').dataset.dirty === 'true') { event.preventDefault(); event.returnValue = ''; }
    });
    document.getElementById('add-service-btn').addEventListener('click', () => {
        addServiceRow(); markEditorDirty();
        document.querySelector('#custom-services .custom-service:last-child input').focus();
    });
    document.getElementById('new-node-btn').addEventListener('click', () => openEditor());
    document.getElementById('cancel-node-btn').addEventListener('click', async () => {
        if (!await mayReplaceDraft()) return;
        fillForm(currentServer());
        switchPanel(state.servers.length ? 'nodes' : 'overview');
    });
    document.getElementById('delete-node-btn').addEventListener('click', deleteServer);
    document.getElementById('geo-node-btn').addEventListener('click', autoGeo);
    document.getElementById('device-geo-btn').addEventListener('click', deviceGeo);
    formField('type').addEventListener('change', updateGeoMode);
    formField('auto_geo').addEventListener('change', updateGeoMode);
    formField('lat').addEventListener('input', updateGeoMode);
    formField('lon').addEventListener('input', updateGeoMode);
    document.getElementById('refresh-status-btn').addEventListener('click', refreshStatus);
    document.getElementById('reload-config-btn').addEventListener('click', reloadConfig);
    document.getElementById('export-config-btn').addEventListener('click', exportRegistry);
    document.getElementById('lock-room-btn').addEventListener('click', async () => {
        if (!await mayReplaceDraft()) return;
        markEditorDirty(false);
        window.location.href = '/logout';
    });

    document.getElementById('copy-access-btn').addEventListener('click', async () => {
        await navigator.clipboard.writeText(document.getElementById('access-url').value);
        setSettingsStatus(t('accessCopied'));
    });
    document.getElementById('reveal-token-btn').addEventListener('click', () => {
        const tokenInput = document.getElementById('access-token');
        tokenInput.type = tokenInput.type === 'password' ? 'text' : 'password';
    });
}

window.addEventListener('overseer:prefs', () => {
    renderOverseerControlPanel();
});

if (typeof document !== 'undefined') {
    initDeckEvents();
    fillForm(currentServer());
    renderOverseerControlPanel();
    try {
        const saved = sessionStorage.getItem('overseer.savedNode');
        sessionStorage.removeItem('overseer.savedNode');
        const node = state.servers.find(server => server.id === saved);
        if (node) {
            selectServer(saved); switchPanel('nodes');
            document.getElementById('node-list-feedback').textContent = t('nodeSaved', {name: node.name});
        }
    } catch { /* Storage may be disabled. */ }
}
