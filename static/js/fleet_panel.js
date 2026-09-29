(() => {
    const fleetState = { summary: null, details: {}, diagnostics: {}, operations: [], busy: false, plan: null };
    const el = id => document.getElementById(id);
    const html = escapeHtml;
    const time = value => new Date(value).toLocaleTimeString(overseerLang(), { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    const stale = value => !value?.checked_at || Date.now() - Date.parse(value.checked_at) > 60000;
    const health = value => stale(value) ? 'unknown' : (value.health || 'unknown');

    async function api(path, body) {
        const response = await fetch('/api/v1' + path, {
            method: body === undefined ? 'GET' : 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: body === undefined ? undefined : JSON.stringify(body),
            signal: AbortSignal.timeout(80000),
        });
        const result = await response.json();
        if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : t('connectionFailed'));
        return result;
    }

    function alertText(alert) {
        return [t(alert.code), alert.service, alert.metric ? t(alert.metric.replace('_percent', '')) : null,
            alert.value == null ? null : String(alert.value)].filter(Boolean).join(' / ');
    }

    function render() {
        window.applyOverseerStatuses?.(window.OVERSEER_STATUSES || {});
        const selected = currentServer();
        const select = el('service-node');
        if (select.options.length !== state.servers.length) {
            select.innerHTML = state.servers.map(s => `<option value="${html(s.id)}">${html(s.name)}</option>`).join('');
        }
        if (selected) select.value = selected.id;
        select.disabled = !selected;
        if (!selected) {
            el('inspect-node-btn').disabled = true;
            el('diagnose-btn').disabled = true;
        }
        const nodes = fleetState.summary?.nodes || [];
        el('fleet-overview').innerHTML = nodes.map(n => `
            <button type="button" class="fleet-row" data-fleet-node="${html(n.id)}">
                <span class="health-dot ${health(n)}"></span><span>${html(n.name || n.id)}</span>
                <span class="health-text ${health(n)}">${html(t('health_' + health(n)))}</span>
                ${n.alerts.length ? `<small>${n.alerts.map(a => html(alertText(a))).join('<br>')}</small>` : ''}
            </button>`).join('');
        const detail = fleetState.details[selected?.id];
        if (!detail) {
            el('service-detail').innerHTML = `<p>${html(t(selected ? 'waiting' : 'addFirstNode'))}</p>`;
        } else {
            const m = detail.metrics;
            el('service-detail').innerHTML = `
                <div class="service-meta"><strong class="health-text ${health(detail)}">${html(t('health_' + health(detail)))}</strong>
                    <span>${html(t('snapshotAt'))} ${html(time(detail.checked_at))}${stale(detail) ? ' / ' + html(t('staleSnapshot')) : ''}</span></div>
                ${detail.environment === 'production' ? `<p class="policy-note">${html(t('protectedWorkload'))}</p>` : ''}
                ${detail.error ? `<p class="error">${html(t(detail.error))}</p>` : ''}
                ${m ? `<div class="resource-strip">${['cpu', 'ram', 'disk'].map(k => `<div><span>${html(t(k))}</span><strong>${Number(m[k + '_percent']).toFixed(1)}%</strong></div>`).join('')}</div>` : ''}
                ${detail.alerts.map(a => `<p class="health-text degraded">${html(alertText(a))}</p>`).join('')}
                <div class="service-list">${detail.services.map(s => `
                    <div class="service-row"><div><strong>${html(s.label)}</strong>
                        <small class="advanced-fields">${html(s.target)} / PID ${html(s.pid ?? '--')} / FD ${html(s.fd_count ?? '--')}</small></div>
                        <span class="health-text ${['active', 'running'].includes(s.state) && !['unhealthy', 'starting'].includes(s.health) ? 'healthy' : 'degraded'}">${html(s.health === 'unhealthy' ? 'Unhealthy' : t('service_' + s.state))}</span>
                        ${s.restart_blocked ? '' : `<button type="button" data-plan-service="${html(s.id)}">${html(t('planRestart'))}</button>`}</div>`).join('') || `<p>${html(t('noServices'))}</p>`}</div>
                ${detail.cores.length ? `<div class="core-list advanced-fields">${detail.cores.map(c => `<small>${html(c.name)} / PID ${html(c.pid)} / FD ${html(c.fd_count ?? '--')}</small>`).join('')}</div>` : ''}`;
        }
        const diag = fleetState.diagnostics[selected?.id];
        el('diagnostic-results').innerHTML = diag ? `
            <p class="policy-note">${html(t('directProbeScope'))}<br>${html(time(diag.checked_at))} / ${html(diag.group)}</p>
            ${diag.checks.map(c => `<div class="diagnostic-row"><strong>${html(c.host)}</strong>
                <span>${html(t('diag_' + c.outcome))} ${c.http_status || ''} / ${c.elapsed_ms} ms</span>
                <small>${html(c.tls || '')} / DNS: ${html(c.dns.join(', ') || '--')}</small></div>`).join('')}` : '';
        const history = fleetState.operations.filter(o => o.server_id === selected?.id);
        el('operation-history').innerHTML = history.slice(0, 6).map(o => `
            <div class="diagnostic-row"><strong>${html(o.target)} / ${html(t('op_' + o.state))}</strong>
                <small>${html(time(o.created_at))} / ${html(o.id.slice(0, 8))}</small>
                ${o.error ? `<span>${html(t(o.error))}</span>` : ''}
                ${o.state === 'unknown' ? `<button type="button" data-reconcile="${html(o.id)}">${html(t('reconcile'))}</button>` : ''}</div>`).join('') || `<p>${html(t('noOperations'))}</p>`;
    }

    async function loadDetails(refresh = false) {
        const id = currentServer()?.id;
        if (!id) return;
        fleetState.details[id] = await api('/nodes/' + encodeURIComponent(id) + '?refresh=' + refresh);
        render();
    }

    async function poll(refresh = false) {
        if (fleetState.busy) return;
        fleetState.busy = true;
        try {
            const [summary, history] = await Promise.all([api('/fleet?refresh=' + refresh), api('/operations')]);
            fleetState.summary = summary;
            fleetState.operations = history.operations;
            window.OVERSEER_FLEET = summary;
            await loadDetails();
            el('service-feedback').textContent = '';
        } catch (err) {
            el('service-feedback').textContent = t(err.message) || t('connectionFailed');
        } finally {
            fleetState.busy = false;
            render();
        }
    }

    async function withButton(button, work) {
        button.disabled = true;
        el('service-feedback').textContent = t('polling');
        try { await work(); el('service-feedback').textContent = ''; }
        catch (err) { el('service-feedback').textContent = t(err.message) || t('connectionFailed'); }
        finally { button.disabled = false; }
    }

    el('service-node').addEventListener('change', event => selectServer(event.target.value));
    el('fleet-overview').addEventListener('click', event => {
        const button = event.target.closest('[data-fleet-node]');
        if (button) { selectServer(button.dataset.fleetNode); switchPanel('services'); }
    });
    el('inspect-node-btn').addEventListener('click', event => withButton(event.currentTarget, async () => {
        await loadDetails(true);
        await poll();
    }));
    el('diagnose-btn').addEventListener('click', event => withButton(event.currentTarget, async () => {
        const id = currentServer()?.id;
        if (!id) return;
        fleetState.diagnostics[id] = await api('/nodes/' + encodeURIComponent(id) + '/diagnostics', { group: el('diagnostic-group').value });
        render();
    }));
    el('service-detail').addEventListener('click', event => {
        const button = event.target.closest('[data-plan-service]');
        if (!button) return;
        withButton(button, async () => {
            const id = currentServer().id;
            fleetState.plan = await api('/nodes/' + encodeURIComponent(id) + '/actions/plan', { service_id: button.dataset.planService });
            const plan = fleetState.plan;
            el('operation-plan').innerHTML = `<strong>${html(currentServer().name)} / ${html(plan.target)}</strong>
                <p>${html(t('restartImpact'))}</p><small>${html(plan.id)}</small>`;
            el('operation-dialog').showModal();
        });
    });
    el('cancel-operation').addEventListener('click', () => el('operation-dialog').close());
    el('operation-history').addEventListener('click', event => {
        const button = event.target.closest('[data-reconcile]');
        if (button) withButton(button, async () => {
            await api('/operations/' + button.dataset.reconcile + '/reconcile', {});
            await poll();
        });
    });
    el('confirm-operation').addEventListener('click', event => withButton(event.currentTarget, async () => {
        const plan = fleetState.plan;
        if (!plan) return;
        fleetState.plan = null;
        el('operation-dialog').close();
        const result = await api('/operations/' + plan.id + '/execute', { confirm: plan.id });
        await poll();
        if (result.state !== 'succeeded') throw new Error('operationUncertain');
    }));
    window.refreshOverseerFleet = () => poll(true);
    window.addEventListener('overseer:selection', () => {
        render();
        loadDetails().catch(err => { el('service-feedback').textContent = t(err.message); });
    });
    window.addEventListener('overseer:prefs', render);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });
    setInterval(() => { if (!document.hidden) poll(); }, 30000);
    setInterval(() => { if (!document.hidden) render(); }, 10000);
    render();
    poll();
})();
