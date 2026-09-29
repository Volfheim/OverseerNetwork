(() => {
    const el = id => document.getElementById(id);
    let templates = {};
    let trustedKey = null;
    let candidateKey = null;
    let connectionRevision = 0;

    function confirmAction(message) {
        const dialog = el('setup-dialog');
        if (dialog.open) return Promise.resolve(false);
        el('setup-confirm-message').textContent = message;
        dialog.returnValue = 'cancel';
        dialog.showModal();
        return new Promise(resolve => dialog.addEventListener('close', () => resolve(dialog.returnValue === 'confirm'), {once: true}));
    }
    window.overseerConfirm = confirmAction;

    async function api(path, method = 'GET', body) {
        const response = await fetch('/api/setup' + path, {
            method, headers: { 'Content-Type': 'application/json' },
            ...(body === undefined ? {} : { body: JSON.stringify(body) }),
            signal: AbortSignal.timeout(25000),
        });
        const data = await response.json();
        if (!response.ok) {
            const detail = Array.isArray(data.detail) ? data.detail.map(v => v.msg).join('; ') : data.detail;
            throw new Error(t(detail || 'connectionFailed'));
        }
        return data;
    }

    function feedback(id, message, error = false) {
        el(id).textContent = message;
        el(id).classList.toggle('setup-error', error);
    }

    async function busy(id, output, action) {
        const button = el(id);
        const revision = connectionRevision;
        button.disabled = true;
        if (output === 'connection-feedback') {
            el('scan-host-key').disabled = el('test-connection').disabled = true;
        }
        try { await action(); }
        catch (error) {
            if (output !== 'connection-feedback' || revision === connectionRevision) {
                feedback(output, ['TimeoutError', 'AbortError'].includes(error.name) ? t('probe_timeout') : error.message || t('connectionFailed'), true);
            }
        }
        finally {
            button.disabled = ['delete-template', 'update-template'].includes(id) && !el('server-template').value;
            if (output === 'connection-feedback') el('scan-host-key').disabled = el('test-connection').disabled = false;
        }
    }

    function validConnection(withUser = true) {
        for (const name of withUser ? ['host', 'port', 'username'] : ['host', 'port']) {
            const input = formField(name);
            if (!input.value.trim() || !input.checkValidity()) {
                feedback('connection-feedback', t('connectionFieldsRequired'), true);
                input.focus(); input.reportValidity();
                return false;
            }
        }
        const host = formField('host');
        if (/[\s/@]/.test(host.value.trim())) {
            feedback('connection-feedback', t('hostOnly'), true);
            host.focus(); return false;
        }
        return true;
    }

    function renderTemplates(selected = el('server-template').value) {
        el('server-template').replaceChildren(new Option(t('noTemplate'), ''));
        Object.entries(templates).forEach(([id, item]) => el('server-template').add(new Option(item.name, id)));
        el('server-template').value = templates[selected] ? selected : '';
        ['apply-template', 'update-template', 'delete-template'].forEach(id => {
            el(id).disabled = !el('server-template').value;
        });
    }

    async function loadTemplates(selected) {
        templates = (await api('/templates')).templates;
        renderTemplates(selected);
        if (selected !== undefined) el('template-name').value = templates[selected]?.name || '';
    }

    function templateFromForm(name) {
        const payload = payloadFromForm();
        return Object.fromEntries(['role', 'tags', 'environment', 'control_mode', 'protected', 'protected_targets', 'services']
            .map(key => [key, payload[key]]).concat([['name', name]]));
    }

    function connectionPayload() {
        return {
            server_id: formField('id').disabled && !formField('clear_key').checked ? formField('id').value : null,
            host: formField('host').value.trim(), port: Number(formField('port').value || 22),
            username: formField('username').value.trim(), key_path: formField('key_path').value.trim() || null,
            host_key: trustedKey,
        };
    }

    function resetConnection() {
        connectionRevision++;
        trustedKey = candidateKey = null;
        el('host-key-review').hidden = true;
        el('trust-host-key').checked = false;
        feedback('connection-feedback', '');
    }

    function fill(server) {
        resetConnection();
        trustedKey = server?.host_key || null;
        if (trustedKey) feedback('connection-feedback', t('keyPinned'));
        const key = formField('key_path');
        key.dataset.i18nPlaceholder = server?.has_key ? 'savedKeyPlaceholder' : 'keyPreserved';
        key.placeholder = t(key.dataset.i18nPlaceholder);
        renderTemplates(server?.profile);
        el('template-name').value = templates[server?.profile]?.name || '';
        feedback('template-feedback', '');
    }

    document.querySelectorAll('[data-create-node]').forEach(button => button.addEventListener('click', () => openEditor(null, button.dataset.createNode)));
    el('first-run').hidden = state.servers.length > 0;
    el('server-template').addEventListener('change', () => {
        renderTemplates();
        el('template-name').value = templates[el('server-template').value]?.name || '';
    });
    el('apply-template').addEventListener('click', async () => {
        const id = el('server-template').value;
        const item = templates[id];
        if (!item || !await confirmAction(t('templateReplace'))) return;
        for (const key of ['role', 'environment', 'control_mode']) formField(key).value = item[key];
        formField('tags').value = item.tags.join(', ');
        formField('profile').value = id;
        formField('protected').checked = item.protected;
        formField('protected_targets').value = item.protected_targets.join('\n');
        el('custom-services').replaceChildren();
        item.services.forEach(addServiceRow);
        markEditorDirty();
        el('services-options').open = true;
        feedback('template-feedback', t('templateApplied'));
    });
    for (const [button, update] of [['save-template', false], ['update-template', true]]) {
        el(button).addEventListener('click', () => busy(button, 'template-feedback', async () => {
            const id = update ? el('server-template').value : 'tpl_' + crypto.randomUUID().slice(0, 8);
            if (update && (!templates[id] || !await confirmAction(t('templateOverwrite')))) return;
            const name = el('template-name').value.trim();
            if (!name) { el('template-name').focus(); throw new Error(t('templateName')); }
            await api('/templates/' + encodeURIComponent(id), 'PUT', templateFromForm(name.trim()));
            await loadTemplates(id);
            formField('profile').value = id;
            markEditorDirty();
            feedback('template-feedback', t('templateSaved'));
        }));
    }
    el('delete-template').addEventListener('click', () => busy('delete-template', 'template-feedback', async () => {
        const id = el('server-template').value;
        if (!templates[id] || !await confirmAction(t('deleteTemplate') + ': ' + templates[id].name + '?')) return;
        await api('/templates/' + encodeURIComponent(id), 'DELETE');
        await loadTemplates();
        feedback('template-feedback', t('templateDeleted'));
    }));
    for (const key of ['host', 'port']) formField(key).addEventListener('input', resetConnection);
    for (const key of ['username', 'key_path', 'clear_key']) formField(key).addEventListener('input', () => {
        connectionRevision++;
        feedback('connection-feedback', t('connectionChanged'));
    });
    formField('type').addEventListener('change', resetConnection);
    el('scan-host-key').addEventListener('click', () => busy('scan-host-key', 'connection-feedback', async () => {
        if (!validConnection(false)) return;
        const revision = connectionRevision;
        feedback('connection-feedback', t('checkingConnection'));
        const result = await api('/host-key', 'POST', connectionPayload());
        if (revision !== connectionRevision) return;
        candidateKey = result.public_key;
        el('host-key-fingerprint').textContent = result.host + ':' + result.port + '\n' + result.fingerprint;
        el('trust-host-key').checked = false;
        el('host-key-review').hidden = false;
        feedback('connection-feedback', t('trustRequired'));
    }));
    el('trust-host-key').addEventListener('change', () => {
        trustedKey = el('trust-host-key').checked ? candidateKey : null;
        feedback('connection-feedback', t(el('trust-host-key').checked ? 'keyNotSaved' : 'trustRequired'));
    });
    el('test-connection').addEventListener('click', () => busy('test-connection', 'connection-feedback', async () => {
        if (!validConnection()) return;
        if (candidateKey && !el('trust-host-key').checked) throw new Error(t('trustRequired'));
        const revision = connectionRevision;
        feedback('connection-feedback', t('checkingConnection'));
        const result = await api('/connection', 'POST', connectionPayload());
        if (revision === connectionRevision) feedback('connection-feedback', t(result.probe_runtime === 'ok' ? 'sshReady' : 'pythonMissing'), result.probe_runtime !== 'ok');
    }));
    el('import-config-btn').addEventListener('click', () => el('import-config-file').click());
    el('import-config-file').addEventListener('change', () => busy('import-config-btn', 'settings-status', async () => {
        const file = el('import-config-file').files[0];
        el('import-config-file').value = '';
        if (!file) return;
        if (file.size > 1048576) throw new Error(t('importTooLarge'));
        const data = JSON.parse(await file.text());
        const preview = await api('/import?preview=true', 'POST', data);
        if (!await mayReplaceDraft()) return;
        if (!await confirmAction(t('importConfirm', preview))) return;
        await api('/import?preview=false', 'POST', data);
        markEditorDirty(false);
        window.location.reload();
    }));
    window.overseerSetup = { fill, validConnection, hostKey: () => trustedKey, pendingTrust: () => candidateKey && !el('trust-host-key').checked };
    window.addEventListener('overseer:prefs', () => renderTemplates());
    fill(currentServer());
    loadTemplates(currentServer()?.profile).catch(error => feedback('template-feedback', error.message, true));
})();
