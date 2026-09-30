# Overseer Network

<p align="center">
  <img src="static/img/overseer-icon.svg" width="96" alt="Overseer Network icon">
</p>

<p align="center"><strong>A local operations room for personal infrastructure</strong><br>
SSH nodes, services, metrics, topology and controlled actions in one web interface.</p>

<p align="center">
  <a href="README.md">Русская версия</a> ·
  <a href="docs/management-api.md">API contract</a> ·
  <a href="LICENSE">MIT License</a>
</p>

<p align="center">
  <code>Python 3.11+</code> · <code>FastAPI</code> · <code>SSH</code> · <code>systemd / Docker</code>
</p>

![Overseer Network concept](docs/overseer-overview.svg)

Overseer Network is a single-user control plane for observing personal infrastructure. It places node health on a 3D globe, collects bounded metrics, opens an SSH terminal and routes permitted actions through an explicit plan and confirmation step.

This repository contains source code, documentation, tests and a redacted synthetic example only. Server inventories, keys, tokens, logs, databases and exports are deliberately excluded from the public copy.

<p align="center">
  <img src="docs/screenshots/real-ui-synthetic.png" width="100%" alt="Real Overseer Network interface with anonymized nodes">
</p>

<p align="center"><sub>Real web interface with a synthetic demo profile: 18 nodes, the actual 3D globe and a responsive panel.</sub></p>

## What it provides

| Area | Included |
| --- | --- |
| Observation | SSH node health, CPU/RAM/disk, latency and connection errors |
| Topology | 3D map, physical pins, upstream links, Relief and Tactical modes |
| Control | systemd and Docker services, reusable templates, SSH and host-key checks |
| Safer actions | Pre-action plan, production/protected guards, durable receipts and reconciliation |
| Interface | Russian and English UI, Fallout-inspired and neutral operational themes, responsive layout |
| API | A small authenticated API for automation without embedding a private inventory in code |

## Quick start

Python 3.11+ is required.

~~~
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe -m app --open
~~~

The foreground panel binds to <code>http://127.0.0.1:2077</code> only. Use <code>--port</code> to select another port. Windows also has a background launcher:

~~~
.\overseer.cmd open
.\overseer.cmd status
.\overseer.cmd stop
~~~

A clean installation starts with an empty registry. Add nodes through the UI; the SSH key stays on the backend machine and is never copied into the project.

### Safe demo profile

[examples/demo/servers.yaml](examples/demo/servers.yaml) contains synthetic addresses and coordinates only. Run it outside your working inventory:

~~~
$env:OVERSEER_HOME = (Resolve-Path .\examples\demo).Path
.\venv\Scripts\python.exe -m app --open --port 2078
~~~

The demo file documents the schema. It does not promise reachable nodes and contains no credentials.

To run the anonymized global showcase, point the panel at the demo profile before starting it:

~~~powershell
$env:OVERSEER_HOME = (Resolve-Path .\examples\demo-many).Path
.\venv\Scripts\python.exe -m app --open --port 2078
~~~

## Anonymized interface showcase

The following frames come from the real Overseer Network web interface running with a safe demo profile: the textured 3D globe, relief shading, coordinate grid, links, rotation controls and responsive mobile layout. All names, states and physical pins in these frames are synthetic; no working inventory, IP addresses, keys or credentials are used. The matching 18-node configuration is [examples/demo-many/servers.yaml](examples/demo-many/servers.yaml); records use local mode and their coordinates are display-only for the map.

<p align="center">
  <img src="docs/screenshots/real-globe-rotation.gif" width="72%" alt="Real 3D globe animation with synthetic nodes">
</p>

<p align="center">
  <img src="docs/screenshots/real-ui-mobile.png" width="42%" alt="Responsive mobile globe interface">
</p>

## Architecture

~~~mermaid
flowchart LR
    UI["Responsive web UI<br/>map · panel · terminal"] --> API["FastAPI<br/>authenticated API"]
    API --> CFG["Validated registry<br/>atomic writes"]
    API --> OBS["Bounded observations<br/>SSH · TCP · local metrics"]
    API --> OPS["Operation planner<br/>explicit confirmation"]
    OBS --> NODES["Linux nodes<br/>systemd / Docker"]
    OPS --> NODES
~~~

| Module | Responsibility |
| --- | --- |
| <code>app/config.py</code>, <code>app/paths.py</code> | Validated schema, templates, atomic writes and data paths |
| <code>app/setup_api.py</code> | First-run onboarding, import/export, templates and SSH checks |
| <code>app/ssh_transport.py</code>, <code>app/remote_probe.py</code> | SSH and bounded observations |
| <code>app/fleet.py</code>, <code>app/fleet_profiles.py</code> | Metric cache and operation policy |
| <code>app/operations.py</code>, <code>app/fleet_api.py</code> | Plans, confirmation and receipts |
| <code>app/geolocation.py</code> | IP geolocation, physical pins and freshness |
| <code>static/js/</code>, <code>templates/</code> | Globe, terminal and display preferences |

## Boundaries and privacy

- This is a single-user control plane. Run one worker and never expose the port directly to the internet.
- The terminal has the configured SSH user's privileges; observation mode does not reduce terminal privileges.
- Production/protected services cannot be restarted through the API. Other actions need a fresh plan and explicit confirmation.
- Registry exports contain addresses, coordinates and topology. Treat every export as private.
- <code>servers.yaml</code>, <code>overseer.secrets.json</code>, <code>data/</code>, logs and build artifacts are ignored by Git.
- Automatic geolocation can send a node's public IP to <code>ipwho.is</code>; use manual coordinates for sensitive nodes.
- Three.js, xterm and fonts currently load from CDNs; full offline mode is not claimed.

See [SECURITY.md](SECURITY.md) for the detailed boundaries and [docs/management-api.md](docs/management-api.md) for the API contract.

## Verification

~~~
.\venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\venv\Scripts\python.exe -m unittest discover -s tests -v
node --test tests\*.test.cjs
.\venv\Scripts\python.exe scripts\source_bundle.py --check
~~~

Build the reviewable source archive with the allowlist:

~~~
.\venv\Scripts\python.exe scripts\source_bundle.py
~~~

The script never uploads anything. It excludes local registries, secrets, keys, logs, virtual environments and unattributed legacy media.

## Status

The project is evolving as a personal operations tool. Backend and web UI are separated by an API contract; this repository does not ship a native desktop/APK package.

## License and assets

Code is released under [MIT](LICENSE). Third-party licenses and asset provenance are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). This is an independent project and is not affiliated with Fallout rights holders; MIT does not grant rights to their trademarks.
