const OVERSEER_PREF_KEY = 'overseer.preferences.v1';

const OVERSEER_DEFAULT_PREFS = {
    language: 'ru',
    uiTheme: 'fallout',
    spaceBackground: true,
    crt: true,
    scanlines: true,
    flicker: true,
    audio: true,
    lowPower: false,
    compactDeck: false,
    amberMode: false,
    mapLabels: true,
    mapSurface: 'tactical',
    mapGrid: true,
    mapLinks: true,
    mapRotate: false,
    advancedMode: false,
};

function readOverseerPrefs() {
    try {
        const saved = JSON.parse(localStorage.getItem(OVERSEER_PREF_KEY) || '{}') || {};
        if (!['fallout', 'operations'].includes(saved.uiTheme)) {
            saved.uiTheme = 'fallout';
            saved.mapSurface = 'tactical';
        }
        return {
            ...OVERSEER_DEFAULT_PREFS,
            ...saved
        };
    } catch {
        return { ...OVERSEER_DEFAULT_PREFS };
    }
}

function saveOverseerPrefs(nextPrefs) {
    window.OVERSEER_PREFS = { ...OVERSEER_DEFAULT_PREFS, ...nextPrefs };
    localStorage.setItem(OVERSEER_PREF_KEY, JSON.stringify(window.OVERSEER_PREFS));
    applyOverseerPrefs(window.OVERSEER_PREFS);
    window.dispatchEvent(new CustomEvent('overseer:prefs', { detail: window.OVERSEER_PREFS }));
}

function applyOverseerPrefs(prefs = readOverseerPrefs()) {
    window.OVERSEER_PREFS = { ...OVERSEER_DEFAULT_PREFS, ...prefs };
    const root = document.documentElement;
    const body = document.body;
    if (!body) return;

    root.classList.toggle('theme-amber', Boolean(window.OVERSEER_PREFS.amberMode));
    body.classList.toggle('theme-fallout', window.OVERSEER_PREFS.uiTheme === 'fallout');
    body.classList.toggle('no-space', !window.OVERSEER_PREFS.spaceBackground);
    body.classList.toggle('no-crt', !window.OVERSEER_PREFS.crt);
    body.classList.toggle('no-scanlines', !window.OVERSEER_PREFS.scanlines);
    body.classList.toggle('no-flicker', !window.OVERSEER_PREFS.flicker);
    body.classList.toggle('low-power', Boolean(window.OVERSEER_PREFS.lowPower));
    body.classList.toggle('compact-deck', Boolean(window.OVERSEER_PREFS.compactDeck));
    body.classList.toggle('hide-map-labels', !window.OVERSEER_PREFS.mapLabels);
    body.classList.toggle('advanced-mode', Boolean(window.OVERSEER_PREFS.advancedMode));
}

function initOverseerPrefControls(root = document) {
    const prefs = readOverseerPrefs();
    root.querySelectorAll('[data-pref]').forEach(control => {
        const key = control.dataset.pref;
        if (!(key in OVERSEER_DEFAULT_PREFS)) return;
        if (control.type === 'checkbox') {
            control.checked = Boolean(prefs[key]);
        } else {
            control.value = prefs[key];
        }
        if (control.dataset.prefReady === '1') return;
        control.dataset.prefReady = '1';
        control.addEventListener('change', () => {
            const nextValue = control.type === 'checkbox' ? control.checked : control.value;
            const next = { ...readOverseerPrefs(), [key]: nextValue };
            if (key === 'uiTheme') next.mapSurface = nextValue === 'fallout' ? 'tactical' : 'relief';
            saveOverseerPrefs(next);
            root.querySelectorAll(`[data-pref="${key}"]`).forEach(other => {
                if (other !== control) {
                    if (other.type === 'checkbox') {
                        other.checked = control.checked;
                    } else {
                        other.value = control.value;
                    }
                }
            });
        });
    });
}

window.OVERSEER_DEFAULT_PREFS = OVERSEER_DEFAULT_PREFS;
window.readOverseerPrefs = readOverseerPrefs;
window.saveOverseerPrefs = saveOverseerPrefs;
window.applyOverseerPrefs = applyOverseerPrefs;
window.initOverseerPrefControls = initOverseerPrefControls;

if (document.body) {
    applyOverseerPrefs();
} else {
    document.addEventListener('DOMContentLoaded', () => applyOverseerPrefs(), { once: true });
}
