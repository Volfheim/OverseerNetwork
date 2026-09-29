"""UI onboarding, reusable templates, and portable inventory transfer."""
import asyncio
import json

import asyncssh
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import ServerConfig, ServerTemplate
from app.security import access_control
from app.ssh_transport import ProbeError, connect


class ConnectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    server_id: str | None = None
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(default="", max_length=100)
    key_path: str | None = Field(default=None, max_length=4096)
    host_key: str | None = Field(default=None, max_length=16384)


def validation_message(error):
    if isinstance(error, ValidationError):
        return "; ".join(".".join(map(str, item["loc"])) + ": " + item["msg"]
                         for item in error.errors(include_input=False, include_context=False))
    return str(error)


def build_setup_router(registry, changed):
    router = APIRouter(prefix="/api/setup", dependencies=[Depends(access_control.require_request)])

    @router.get("/templates")
    async def templates():
        return {"templates": {k: v.model_dump() for k, v in registry.templates.items()}}

    @router.put("/templates/{template_id}")
    async def save_template(template_id: str, payload: ServerTemplate):
        try:
            saved = registry.upsert_template(template_id, payload.model_dump())
            return {"template": saved.model_dump(), "id": template_id}
        except ValueError as exc:
            raise HTTPException(400, validation_message(exc)) from exc

    @router.delete("/templates/{template_id}")
    async def delete_template(template_id: str):
        try:
            registry.delete_template(template_id)
        except KeyError as exc:
            raise HTTPException(404, "Template not found") from exc
        return {"status": "ok"}

    @router.get("/export")
    async def export_registry():
        return registry.export_registry()

    @router.post("/import")
    async def import_registry(request: Request, preview: bool = True):
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 1_048_576:
                raise HTTPException(413, "Registry exceeds 1 MiB")
        try:
            result = registry.import_registry(json.loads(raw), preview=preview)
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, validation_message(exc)) from exc
        if not preview:
            changed()
        return result

    @router.post("/host-key")
    async def host_key(payload: ConnectionRequest):
        try:
            key = await asyncio.wait_for(asyncssh.get_server_host_key(payload.host, payload.port), 10)
            if key is None:
                raise ValueError()
            return {"host": payload.host, "port": payload.port,
                    "public_key": key.export_public_key().decode().strip(),
                    "fingerprint": key.get_fingerprint(), "verified": False}
        except (asyncssh.Error, OSError, ValueError, asyncio.TimeoutError) as exc:
            raise HTTPException(502, "ssh_unavailable") from exc

    @router.post("/connection")
    async def test_connection(payload: ConnectionRequest):
        existing = registry.servers.get(payload.server_id)
        pinned_key = payload.host_key
        if pinned_key is None and existing and (existing.host, existing.port) == (payload.host, payload.port):
            pinned_key = existing.host_key
        try:
            node = ServerConfig(name="Connection check", host=payload.host, port=payload.port,
                                username=payload.username,
                                key_path=payload.key_path or (existing.key_path if existing else None),
                                host_key=pinned_key, strict_host_key=True)
            async with connect(node) as conn:
                result = await conn.run("python3 --version", timeout=6, check=False)
            return {"ssh": "ok", "probe_runtime": "ok" if result.exit_status == 0 else "python3_missing"}
        except ValidationError as exc:
            raise HTTPException(400, validation_message(exc)) from exc
        except ProbeError as exc:
            raise HTTPException(502, str(exc)) from exc
        except (asyncssh.Error, OSError, ValueError, asyncio.TimeoutError) as exc:
            raise HTTPException(502, "ssh_check_failed") from exc

    return router
