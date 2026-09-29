const term = new Terminal({
    cursorBlink: true,
    fontFamily: '"Ubuntu Mono", monospace, "Courier New"',
    fontSize: 16,
    theme: {
        background: '#0a0a0a',
        foreground: '#4af626',
        cursor: '#4af626',
        selection: 'rgba(74, 246, 38, 0.3)'
    },
    scrollback: 1200
});

let terminalAudio;
function terminalTone(frequency, duration, volume) {
    if (!window.OVERSEER_PREFS?.audio) return;
    try {
        terminalAudio ||= new (window.AudioContext || window.webkitAudioContext)();
        terminalAudio.resume().catch(() => {});
        const oscillator = terminalAudio.createOscillator();
        const gain = terminalAudio.createGain();
        const now = terminalAudio.currentTime;
        oscillator.type = 'triangle';
        oscillator.frequency.setValueAtTime(frequency, now);
        gain.gain.setValueAtTime(volume, now);
        gain.gain.exponentialRampToValueAtTime(0.001, now + duration);
        oscillator.connect(gain);
        gain.connect(terminalAudio.destination);
        oscillator.start(now);
        oscillator.stop(now + duration);
        oscillator.onended = () => { oscillator.disconnect(); gain.disconnect(); };
    } catch { /* Audio is optional on restricted browser runtimes. */ }
}

window.playTerminalOn = function () {
    terminalTone(440, 0.24, 0.06);
};

function playTypeSound(isEnter = false) {
    terminalTone(isEnter ? 660 : 1100, isEnter ? 0.08 : 0.025, 0.025);
}

const fitAddon = new FitAddon.FitAddon();
term.loadAddon(fitAddon);
term.loadAddon(new WebLinksAddon.WebLinksAddon());
term.open(document.getElementById('terminal-container'));

let ws = null;
let currentServerId = null;
let intentionalClose = false;

function sendResize() {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }));
    }
}

document.getElementById('minimize-terminal-btn').addEventListener('click', event => {
    event.stopPropagation();
    document.getElementById('terminal-modal').classList.add('hidden');
    if (window.resetGlobeZoom) window.resetGlobeZoom();
});

window.cleanupTerminal = function () {
    document.getElementById('terminal-modal').classList.add('hidden');
    intentionalClose = true;
    if (ws) {
        ws.close();
        ws = null;
    }
    term.clear();
    currentServerId = null;
    if (window.resetGlobeZoom) window.resetGlobeZoom();
};

document.getElementById('close-terminal-btn').addEventListener('click', event => {
    event.stopPropagation();
    const serverIdToKill = currentServerId;
    window.cleanupTerminal();

    if (serverIdToKill) {
        fetch(`/api/sessions/${serverIdToKill}`, { method: 'DELETE' })
            .catch(error => console.error('Session shutdown failed:', error));
    }
});

window.openTerminal = function (serverId, serverName) {
    if (currentServerId === serverId && ws && ws.readyState === WebSocket.OPEN) {
        document.getElementById('server-name-label').textContent = serverName.toUpperCase();
        setTimeout(() => {
            fitAddon.fit();
            document.getElementById('terminal-container').setAttribute('tabindex', '0');
            term.focus();
            term.refresh(0, term.rows - 1);
        }, 550);
        return;
    }

    intentionalClose = true;
    if (ws) ws.close();
    ws = null;
    intentionalClose = false;
    term.clear();

    currentServerId = serverId;
    document.getElementById('server-name-label').textContent = serverName.toUpperCase();
    term.writeln('\x1b[32m[SYSTEM] ROBCO TERMINAL INTERFACE INITIALIZED...\x1b[0m');
    term.writeln(`\x1b[32m[SYSTEM] OPENING UPLINK TO ${serverName.toUpperCase()}...\x1b[0m`);

    const modalEl = document.getElementById('terminal-modal');
    if (window._fitTerminalEventListener) {
        modalEl.removeEventListener('transitionend', window._fitTerminalEventListener);
    }

    window._fitTerminalEventListener = function (event) {
        if (event.target === modalEl && event.propertyName === 'opacity' && !modalEl.classList.contains('hidden')) {
            fitAddon.fit();
            sendResize();
            const termContainerEl = document.getElementById('terminal-container');
            termContainerEl.setAttribute('tabindex', '0');
            term.focus();
            term.refresh(0, term.rows - 1);
            modalEl.removeEventListener('transitionend', window._fitTerminalEventListener);
            window._fitTerminalEventListener = null;
        }
    };
    modalEl.addEventListener('transitionend', window._fitTerminalEventListener);

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${protocol}//${window.location.host}/ws/terminal/${serverId}`);

    ws.onopen = () => {
        intentionalClose = false;
        sendResize();
    };

    ws.onmessage = event => {
        if (event.data === 'OVERSEER_ACTION_CLOSE_TERMINAL') {
            intentionalClose = true;
            window.cleanupTerminal();
            return;
        }
        term.write(event.data);
        if (!event.data.includes('\x1b[2J')) playTypeSound(false);
    };

    ws.onclose = () => {
        if (!intentionalClose) {
            term.writeln('\r\n\x1b[31m[CRITICAL] UPLINK CLOSED BY REMOTE OR NETWORK POLICY.\x1b[0m\r\n');
        }
    };

    ws.onerror = () => {
        term.writeln('\r\n\x1b[31m[ERROR] WEBSOCKET UPLINK FAULT.\x1b[0m\r\n');
    };
};

term.onData(data => {
    playTypeSound(data === '\r');
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(data);
    }
});

window.addEventListener('resize', () => {
    if (!document.getElementById('terminal-modal').classList.contains('hidden')) {
        fitAddon.fit();
        sendResize();
    }
});
