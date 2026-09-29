from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict

from app.config import config
from app.paths import data_dir
from app.fleet import FleetManager
from app.fleet_profiles import DIAGNOSTIC_TARGETS
from app.operations import OperationError, Operations
from app.security import access_control
from app.ssh_transport import ProbeError


def authorized(request: Request):
    access_control.require_request(request)


router = APIRouter(prefix="/api/v1", tags=["fleet"], dependencies=[Depends(authorized)])
fleet = FleetManager(config)
operations = Operations(fleet, data_dir() / "operations.sqlite3")


class DiagnosticRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    group: Literal["openai", "google", "youtube", "russian"]


class ActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service_id: str
    action: Literal["restart"] = "restart"


class ExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: str


def require_node(server_id):
    if server_id not in config.servers:
        raise HTTPException(404, "node_not_found")


@router.get("/capabilities")
async def capabilities():
    return {"schema_version": 1, "profiles": list(config.templates), "diagnostic_groups": DIAGNOSTIC_TARGETS,
            "actions": ["restart"], "arbitrary_commands": False, "confirmation_required": True,
            "production_restart": False, "default_control_mode": "observe",
            "summary": "/api/v1/fleet", "inventory": "/api/servers"}


@router.get("/fleet")
async def summary(refresh: bool = False):
    return await fleet.summary(refresh)


@router.get("/nodes/{server_id}")
async def inspect(server_id: str, refresh: bool = False):
    require_node(server_id)
    return await fleet.inspect(server_id, refresh)


@router.post("/nodes/{server_id}/diagnostics")
async def diagnose(server_id: str, payload: DiagnosticRequest):
    require_node(server_id)
    try:
        return await fleet.diagnose(server_id, payload.group)
    except ProbeError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.post("/nodes/{server_id}/actions/plan")
async def plan(server_id: str, payload: ActionRequest):
    require_node(server_id)
    try:
        return await operations.plan(server_id, payload.service_id)
    except OperationError as exc:
        raise HTTPException(exc.status, exc.code) from exc


@router.post("/operations/{operation_id}/execute")
async def execute(operation_id: str, payload: ExecuteRequest):
    try:
        return await operations.execute(operation_id, payload.confirm)
    except OperationError as exc:
        raise HTTPException(exc.status, exc.code) from exc
    except ValueError as exc:
        raise HTTPException(409, "node_removed") from exc


@router.get("/operations")
async def history(limit: int = Query(20, ge=1, le=100)):
    return {"operations": operations.history(limit)}


@router.post("/operations/{operation_id}/reconcile")
async def reconcile(operation_id: str):
    try:
        return await operations.reconcile(operation_id)
    except OperationError as exc:
        raise HTTPException(exc.status, exc.code) from exc
    except ValueError as exc:
        raise HTTPException(409, "node_removed") from exc


@router.get("/operations/{operation_id}")
async def operation(operation_id: str):
    try:
        return operations.get(operation_id)
    except OperationError as exc:
        raise HTTPException(exc.status, exc.code) from exc
