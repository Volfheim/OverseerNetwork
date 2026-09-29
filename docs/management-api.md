# Overseer management API

Overseer runs on the administrator PC. The existing SSH keys stay there. A small
stdlib Python probe is streamed over SSH and returns selected JSON fields; no
agent, listener, configuration, or credential is installed on the VPS.

## Command-line client

For the normal local lifecycle, use the `overseer` command from any PowerShell or
Command Prompt. It starts only the local panel, verifies that it is ready, and
never stops an unknown process occupying port 2077:

```powershell
overseer                 # start if needed and open the panel
overseer start            # start without opening a browser
overseer status           # running, starting/stopping, stopped, or unmanaged listener
overseer stop             # graceful local panel shutdown
overseer restart          # graceful local restart
.\venv\Scripts\python.exe scripts\overseerctl.py summary
.\venv\Scripts\python.exe scripts\overseerctl.py inspect example-node
.\venv\Scripts\python.exe scripts\overseerctl.py diagnose example-node openai
.\venv\Scripts\python.exe scripts\overseerctl.py history
```

The Start menu contains two direct entries: `Overseer Network - Открыть панель`
and `Overseer Network - Остановить панель`. Small Windows GUI launchers call
`pythonw.exe` without a console. The installer builds them with the Windows .NET
Framework compiler into `data/launcher`. To recreate
the shortcuts after moving the checkout, run:

```powershell
.\scripts\install_shortcuts.ps1
```

If Windows does not discover per-user Start menu entries, install the same two
shortcuts into the shared Start menu from an elevated PowerShell with
`.\scripts\install_shortcuts.ps1 -AllUsers`. This removes this checkout's duplicate
per-user entries only after the shared shortcuts have been created.

Launching Uvicorn directly is for development only: it is intentionally not
adopted or stopped by `overseer` because it cannot prove ownership.

`summary` is the normal first call. Use `inspect <id>` only for relevant nodes,
and `--refresh` when a new measurement is needed. The client reads the existing
panel token from `overseer.secrets.json` into a Bearer header; it never prints it.
The default response is compact JSON. `--pretty` and `--url` precede the command.
Plain HTTP is accepted only for loopback, and redirects/proxies are disabled.

The REST client works from any shell or automation tool without additional
plugins or credentials.

## Endpoints

All endpoints require the existing panel cookie or `Authorization: Bearer ...`.

| Method and path | Result |
|---|---|
| `GET /api/v1/capabilities` | Profiles, supported diagnostics and operation boundaries |
| `GET /api/v1/fleet?refresh=false` | Compact fleet snapshot, resource percentages, alerts |
| `GET /api/v1/nodes/{id}?refresh=false` | Services, process identities, FD counts, timestamps |
| `POST /api/v1/nodes/{id}/diagnostics` | `{ "group": "openai" }`; also google, youtube, russian |
| `POST /api/v1/nodes/{id}/actions/plan` | `{ "service_id": "...", "action": "restart" }` |
| `POST /api/v1/operations/{id}/execute` | `{ "confirm": "<same operation ID>" }` |
| `GET /api/v1/operations/{id}` | Durable receipt; use after a lost HTTP response |
| `POST /api/v1/operations/{id}/reconcile` | Read current health after an unknown outcome; never repeats the action |
| `GET /api/v1/operations?limit=20` | Recent operation receipts |
| `GET/POST /api/servers`, `PUT/DELETE /api/servers/{id}` | Existing UI-backed registry management |
| `GET /api/setup/templates`, `PUT/DELETE /api/setup/templates/{id}` | Reusable service/policy templates; existing nodes never inherit later edits |
| `GET /api/setup/export` | Portable private inventory, without private-key paths |
| `POST /api/setup/import?preview=true` | Validate schema/topology/conflicts; `preview=false` commits the same validated import atomically |
| `POST /api/setup/host-key` | Read untrusted public key and fingerprint; does not store trust |
| `POST /api/setup/connection` | Read-only SSH authentication and Python 3 check |

## Semantics

- Cache TTL: 30 seconds, at most four simultaneous remote operations. Responses
  expose `checked_at`, `age_seconds`, `cached`, and schema version 1. Nothing is
  monitored while Overseer is stopped; detailed probes run on API/UI demand.
- `healthy` means the monitored services/resources passed this snapshot. It does
  not prove application, client VPN, WARP, WireGuard handshake, website content,
  or mobile performance. `unknown` includes SSH/auth/host-key/probe failures.
- Resource warning thresholds: RAM/disk >=90%, Xray/rw-core FD count >=8000.
  A high FD count is a diagnostic signal, not a proven leak or restart instruction.
- Diagnostics use DNS and TLS/HTTP HEAD from the selected machine's default
  egress. HTTP 403 and Cloudflare challenges are responses, not proof of DPI.
  Client Smart/Global routing and account behavior require separate tests.
- A local node is the PC running the backend, not an arbitrary visiting phone.
- `upstream_id` is configured topology, not evidence of a healthy traffic path.
- Templates are editable data, copied to nodes through the UI. There are no
  built-in owner-specific service lists. Node health is not inferred from TCP22.
- The `/api/v1` endpoints do not accept arbitrary shell commands, VPN deployment
  payloads, or restart of protected production workloads. The existing interactive
  SSH terminal still has the privileges of the configured SSH account; these
  operation guards are not a separate read-only authentication role.
- Restarts require an unprotected non-production SSH node, `control_mode=operate`,
  `allow_restart=true` on the exact service, and verified SSH host keys. Known
  management services and per-node `protected_targets` cannot be unlocked by renaming their service ID.
- A plan expires in 120 seconds and binds to config and observed process identity.
  Execution claims the operation in SQLite before sending a command. Repeating
  its ID returns the receipt; it does not repeat the restart. Uncertain outcomes
  block new plans for that service. Reconciliation can record observed recovery,
  but retains `execution_confirmed=false` and never calls it successful execution.
- Use one Uvicorn worker. The registry/cache are process-local; durable operation
  receipts live in `data/operations.sqlite3`. Do not delete that database to retry.
- `OVERSEER_HOME` selects a separate runtime directory shared by the app, launcher
  and CLI. Imports are additive only; all imported nodes become observe-only with
  host verification enabled and pinned host keys cleared. Exports are private.

## Verification

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\venv\Scripts\python.exe -m unittest discover -s tests -v
```

Tests isolate the registry and SSH runner: authorization, stale/missing services,
cache, atomic registry failures, protected workloads, changed/expired plans,
concurrent retries, persisted receipts and unknown outcomes. Real production
restarts are not part of verification.
