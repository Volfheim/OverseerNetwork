# Overseer Network Architecture Roadmap

## Product Direction

Overseer Network should become a Fallout-styled situation center for personal infrastructure:

- global node map with live state,
- local HQ diagnostics for the current PC,
- SSH terminal access,
- server registry management from UI,
- automatic IP geolocation,
- future VPN service configuration workflows,
- desktop and mobile shells around the same control-plane API.

## Website, Desktop App, Or Android App?

Recommended sequence:

1. Keep the core as a local web control plane first.
   - FastAPI is already a good fit for SSH, telemetry, config persistence, and future VPN operations.
   - The same responsive UI can serve PC and phone; mobile access additionally
     needs a deliberately configured HTTPS/VPN endpoint. Default bind is loopback.
   - Iteration is much faster than native desktop/mobile rebuilds.

2. Make it installable as a PWA.
   - This is the best near-term Android path: open through HTTPS/VPN, add to home screen, run fullscreen.
   - It keeps one frontend codebase and avoids Play Store/APK complexity while the product is still changing.

3. Wrap the same web app as a desktop application later.
   - Prefer Tauri for a small Windows desktop shell, tray icon, autostart, native notifications, and secure local storage.
   - Use Electron only if future UI dependencies require a heavier Chromium/Node environment.

4. Wrap mobile only when native features are truly needed.
   - Capacitor is the practical bridge if push notifications, QR enrollment, Android secure storage, or native sharing become important.
   - A full native Android app is not worth it yet because most value is in the control plane and server operations, not Android-specific UI.

So the best product shape is: web/PWA now, Tauri desktop shell next, Capacitor Android wrapper later if the PWA hits real limits.

## Current Foundation

The project is now still a FastAPI web app with a Three.js/Xterm front end, but the control plane has a clearer split:

- `app/config.py` owns validated server/template data and atomic YAML persistence.
- `app/paths.py` isolates runtime files through `OVERSEER_HOME`.
- `app/setup_api.py` exposes template CRUD, portable inventory and SSH onboarding.
- `app/security.py` owns local access token creation and request/WebSocket validation.
- `app/system_probe.py` owns local metrics and TCP checks.
- `app/geolocation.py` owns IP lookup, physical pins and freshness.
- `app/fleet.py` and `app/remote_probe.py` collect remote service/resource snapshots
  on demand over SSH without installing an agent.
- `app/operations.py` implements confirmed, policy-gated restarts and durable receipts.
- `app/session_manager.py` owns terminal session lifecycle.
- `app/main.py` exposes protected APIs for status, servers, geolocation, metrics, and terminals.

## Next Functional Layers

1. VPN node profile editor:
   - WireGuard/OpenVPN/sing-box profile type,
   - port, peer, DNS, route and allowed-IP fields,
   - read-only config preview,
   - guarded apply/restart action over SSH.

2. Optional persistent telemetry:
   - continuous history and alerts when the web page is closed,
   - application-specific health adapters and VPN client-path checks,
   - an installed agent only if SSH-on-demand no longer meets the need.

3. Desktop shell:
   - package the current web UI in Tauri or Electron,
   - local tray entry,
   - auto-start,
   - local-only secret storage,
   - one-click LAN/mobile access link.

4. Mobile path:
   - first ship as PWA over HTTPS through the user's VPN,
   - later wrap with Capacitor if push notifications, native share, or secure storage become necessary.

5. Safer operations:
   - per-action confirmation for destructive commands,
   - audit log for config changes and session starts,
   - per-node permissions and optional short-lived tokens.
