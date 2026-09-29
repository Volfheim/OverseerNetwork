import asyncio
import json
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import Body, Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from loguru import logger

from app.config import PROJECT_ROOT, ServerConfig, config
from app.fleet_api import fleet, router as fleet_router
from app.geolocation import GeoLocator, lookup_geo
from app.security import COOKIE_NAME, access_control
from app.session_manager import session_manager
from app.system_probe import check_tcp, collect_local_metrics
from app.paths import runtime_home
from app.setup_api import build_setup_router, validation_message


logger.remove()
logger.add(
    sys.stdout,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
)
logger.add(
    runtime_home() / "overseer.log",
    rotation="10 MB",
    retention="7 days",
    compression="zip",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
    level="INFO",
)

@asynccontextmanager
async def lifespan(app):
    await startup_event()
    try:
        yield
    finally:
        await shutdown_event()


app = FastAPI(title="Overseer Network", lifespan=lifespan)
app.include_router(fleet_router)
app.include_router(build_setup_router(config, fleet.invalidate))
app.mount("/static", StaticFiles(directory=str(PROJECT_ROOT / "static")), name="static")
templates = Jinja2Templates(directory=str(PROJECT_ROOT / "templates"))

status_clients: List[WebSocket] = []
geolocator = GeoLocator()


def public_servers():
    return [geolocator.public_node(server_id, node) for server_id, node in config.servers.items()]


def require_access(request: Request) -> None:
    access_control.require_request(request)


def render_login() -> HTMLResponse:
    return HTMLResponse(
        """
<!doctype html>
<html lang="ru" translate="no">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#020402">
  <meta name="google" content="notranslate">
  <title>Overseer Access</title>
  <link rel="manifest" href="/static/manifest.webmanifest">
  <link rel="stylesheet" href="/static/css/style.css?v=12">
  <script src="/static/js/preferences.js?v=15"></script>
  <script src="/static/js/i18n.js?v=15"></script>
</head>
<body class="crt login-screen">
  <div class="scanlines"></div>
  <main class="login-shell">
    <section class="login-card">
      <p class="login-kicker" data-i18n="loginKicker">ROBCO INDUSTRIES: защищенный вход</p>
      <h1 data-i18n="loginTitle">Доступ Смотрителя</h1>
      <form method="get" action="/">
        <label><span data-i18n="controlToken">Ключ доступа</span>
          <input name="access_token" type="password" autocomplete="current-password" autofocus data-i18n-placeholder="tokenPlaceholder">
        </label>
        <button type="submit" data-i18n="unlock">Открыть панель</button>
      </form>
    </section>
    <aside class="login-settings">
      <div class="deck-title">
        <span data-i18n="displaySettings">Настройки вида</span>
        <strong data-i18n="localOnly">Локально</strong>
      </div>
      <div class="settings-grid">
        <label><input type="checkbox" data-pref="crt"> <span data-i18n="crtGlass">CRT-стекло</span></label>
        <label><input type="checkbox" data-pref="scanlines"> <span data-i18n="scanlines">Скан-линии</span></label>
        <label><input type="checkbox" data-pref="flicker"> <span data-i18n="flicker">Мерцание</span></label>
        <label><input type="checkbox" data-pref="audio"> <span data-i18n="audio">Звук</span></label>
        <label><input type="checkbox" data-pref="lowPower"> <span data-i18n="lowPower">Экономия</span></label>
        <label><input type="checkbox" data-pref="compactDeck"> <span data-i18n="compactUi">Компактно</span></label>
        <label><input type="checkbox" data-pref="amberMode"> <span data-i18n="amberMode">Янтарный</span></label>
        <label><input type="checkbox" data-pref="mapLabels"> <span data-i18n="mapLabels">Подписи карты</span></label>
        <label><input type="checkbox" data-pref="advancedMode"> <span data-i18n="advancedMode">Расширенный режим</span></label>
        <label><span data-i18n="language">Язык</span>
          <select data-pref="language">
            <option value="ru" data-i18n="russian">Русский</option>
            <option value="en" data-i18n="english">English</option>
          </select>
        </label>
      </div>
      <p class="login-note" data-i18n="loginNote">Эти настройки хранятся только в браузере. Управление серверами откроется после входа.</p>
    </aside>
  </main>
  <script>
    document.addEventListener('DOMContentLoaded', () => window.initOverseerPrefControls(document));
  </script>
</body>
</html>
""",
        status_code=401,
    )


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    token = request.query_params.get("access_token")
    if token:
        if not access_control.validate(token):
            return render_login()
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            COOKIE_NAME,
            token,
            httponly=True,
            samesite="lax",
            max_age=60 * 60 * 24 * 30,
        )
        return response

    if not access_control.validate(request.cookies.get(COOKIE_NAME)):
        return render_login()

    servers = public_servers()
    access_url = f"{request.url.scheme}://{request.url.netloc}/?access_token={access_control.access_token}"
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "servers": servers,
            "access": {
                "local_url": access_url,
                "token": access_control.access_token,
            },
        },
    )


@app.get("/api/security/access", dependencies=[Depends(require_access)])
async def access_info(request: Request):
    access_url = f"{request.url.scheme}://{request.url.netloc}/?access_token={access_control.access_token}"
    return {
        "token": access_control.access_token,
        "local_url": access_url,
    }


@app.get("/logout")
async def logout():
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(COOKIE_NAME)
    return response


@app.post("/api/config/reload", dependencies=[Depends(require_access)])
async def reload_config():
    config.reload_config()
    fleet.invalidate()
    return {"status": "ok", "servers": public_servers()}


@app.get("/api/servers", dependencies=[Depends(require_access)])
async def list_servers():
    return {"servers": public_servers()}


@app.post("/api/servers", dependencies=[Depends(require_access)])
async def create_server(payload: Dict[str, Any] = Body(...)):
    server_id = str(payload.pop("id", "")).strip()
    if not server_id:
        raise HTTPException(status_code=400, detail="server id is required")
    if server_id in config.servers:
        raise HTTPException(status_code=409, detail="server id already exists")
    try:
        server = config.upsert_server(server_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=validation_message(exc)) from exc
    return {"server": server.public_dict(server_id)}


@app.put("/api/servers/{server_id}", dependencies=[Depends(require_access)])
async def update_server(server_id: str, payload: Dict[str, Any] = Body(...)):
    existing = config.servers.get(server_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="server not found")
    payload.pop("id", None)
    if not payload.get("key_path"):
        payload["key_path"] = existing.key_path
    if payload.pop("clear_key", False) or payload.get("type", existing.type) == "local":
        payload["key_path"] = None
    if any(k in payload and payload[k] != getattr(existing, k) for k in ("host", "port")) and "host_key" not in payload:
        payload["host_key"] = None
    payload = {**existing.storage_dict(), **payload}
    try:
        server = config.upsert_server(server_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=validation_message(exc)) from exc
    fleet.invalidate(server_id)
    if any(getattr(existing, key) != getattr(server, key) for key in
           ("type", "host", "port", "username", "key_path", "host_key", "strict_host_key")):
        session_manager.kill_all_for_server(server_id)
    return {"server": server.public_dict(server_id)}


@app.delete("/api/servers/{server_id}", dependencies=[Depends(require_access)])
async def delete_server(server_id: str):
    try:
        config.delete_server(server_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="server not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session_manager.kill_all_for_server(server_id)
    fleet.invalidate(server_id)
    return {"status": "ok"}


@app.delete("/api/sessions/{server_id}", dependencies=[Depends(require_access)])
async def kill_session_endpoint(server_id: str):
    session_manager.kill_session(server_id)
    return {"status": "ok", "message": f"Session {server_id} killed"}


@app.get("/api/status", dependencies=[Depends(require_access)])
async def get_status():
    return getattr(app.state, "server_statuses", {})


@app.get("/api/local/metrics", dependencies=[Depends(require_access)])
async def local_metrics():
    return collect_local_metrics()


@app.post("/api/geo/lookup", dependencies=[Depends(require_access)])
async def geo_lookup(payload: Dict[str, Any] = Body(default={})):
    host = payload.get("host")
    try:
        result = await asyncio.to_thread(lookup_geo, host)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return result


@app.get("/api/geo/nodes", dependencies=[Depends(require_access)])
async def node_locations(refresh: bool = False):
    nodes = list(config.servers.items())
    results = await asyncio.gather(*(geolocator.locate(node, refresh=refresh) for _, node in nodes))
    return {"locations": dict(zip((server_id for server_id, _ in nodes), results))}


async def build_server_status(server_id: str, server: ServerConfig) -> Dict[str, Any]:
    checked_at = datetime.now(timezone.utc).isoformat()
    if server.type == "local":
        metrics = await asyncio.to_thread(collect_local_metrics)
        return {
            "id": server_id,
            "status": "ONLINE",
            "latency_ms": 0,
            "checked_at": checked_at,
            "error": None,
            "metrics": metrics,
        }

    tcp = await check_tcp(server.host, server.port)
    return {
        "id": server_id,
        "status": "ONLINE" if tcp["online"] else "OFFLINE",
        "latency_ms": tcp["latency_ms"],
        "checked_at": checked_at,
        "error": tcp["error"],
        "metrics": None,
    }


async def ping_servers_loop():
    app.state.server_statuses = {}
    while True:
        try:
            nodes = list(config.servers.items())
            results = await asyncio.gather(*(build_server_status(sid, server) for sid, server in nodes))
            statuses = {item["id"]: item for item in results}

            if statuses != app.state.server_statuses:
                app.state.server_statuses = statuses
                for ws in list(status_clients):
                    try:
                        await ws.send_json(statuses)
                    except Exception:
                        if ws in status_clients:
                            status_clients.remove(ws)
        except Exception as exc:
            logger.error(f"Error in ping loop: {exc}")

        await asyncio.sleep(15)


async def startup_event():
    app.state.ping_task = asyncio.create_task(ping_servers_loop())


async def shutdown_event():
    task = getattr(app.state, "ping_task", None)
    if task:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    for server_id in list(session_manager.active_sessions):
        session_manager.kill_session(server_id)


@app.websocket("/ws/status")
async def websocket_status(websocket: WebSocket):
    if not await access_control.require_websocket(websocket):
        return
    await websocket.accept()
    status_clients.append(websocket)
    try:
        if hasattr(app.state, "server_statuses"):
            await websocket.send_json(app.state.server_statuses)
        while True:
            await websocket.receive_text()
    except Exception:
        pass
    finally:
        if websocket in status_clients:
            status_clients.remove(websocket)


@app.websocket("/ws/terminal/{server_id}")
async def websocket_endpoint(websocket: WebSocket, server_id: str):
    if not await access_control.require_websocket(websocket):
        return
    await websocket.accept()

    try:
        server = config.get_server(server_id)
    except ValueError:
        await websocket.send_text("\r\n\x1b[31m[ERROR] NODE NOT FOUND IN OVERSEER REGISTRY\x1b[0m\r\n")
        await websocket.close()
        return

    session = session_manager.get_or_create_session(server_id, server)
    session.websockets.append(websocket)

    if session.history:
        await websocket.send_text(session.history)

    try:
        while True:
            data = await websocket.receive_text()
            if data.startswith("{"):
                try:
                    msg = json.loads(data)
                    if msg.get("type") == "resize":
                        session.resize_pty(int(msg["cols"]), int(msg["rows"]))
                        continue
                except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                    pass
            if len(data) <= 65536:
                await session.stdin_queue.put(data)
    except WebSocketDisconnect:
        logger.info(f"WebSocket client disconnected from server -> {server_id}")
    except Exception as exc:
        logger.error(f"WebSocket error on {server_id}: {exc}")
    finally:
        if websocket in session.websockets:
            session.websockets.remove(websocket)
