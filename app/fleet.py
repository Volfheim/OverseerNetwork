import asyncio
import copy
import hashlib
import json
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from app.fleet_profiles import DIAGNOSTIC_TARGETS, restart_block_reason, services_for
from app.ssh_transport import ProbeError, run_script
from app.system_probe import collect_local_metrics


PROBE_SOURCE = Path(__file__).with_name("remote_probe.py").read_text(encoding="utf-8")
CACHE_SECONDS = 30


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def fingerprint(server):
    return hashlib.sha256(server.model_dump_json().encode()).hexdigest()


async def probe(server, payload):
    argument = json.dumps(payload, separators=(",", ":"))
    if server.type == "local":
        proc = await asyncio.create_subprocess_exec(sys.executable, "-c", PROBE_SOURCE, argument,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), 25)
            if proc.returncode or len(out) > 131072:
                raise ProbeError("local_probe_failed")
            return json.loads(out)
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
    raw = await run_script(server, "python3 - " + shlex.quote(argument), PROBE_SOURCE)
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except ValueError as exc:
        raise ProbeError("invalid_probe_response") from exc


def service_healthy(service):
    return service.get("state") in {"active", "running"} and service.get("health") not in {"unhealthy", "starting"}


class FleetManager:
    def __init__(self, registry, runner=probe):
        self.registry = registry
        self.runner = runner
        self.cache = {}
        self.locks = {}
        self.semaphore = asyncio.Semaphore(4)

    def invalidate(self, server_id=None):
        if server_id:
            self.cache.pop(server_id, None)
        else:
            self.cache.clear()

    async def inspect(self, server_id, refresh=False):
        self.registry.get_server(server_id)
        requested_at = time.monotonic()
        async with self.locks.setdefault(server_id, asyncio.Lock()):
            server = self.registry.get_server(server_id)
            identity = fingerprint(server)
            cached = self.cache.get(server_id)
            if cached and cached[0] == identity and (not refresh or cached[1] >= requested_at) and time.monotonic() - cached[1] < CACHE_SECONDS:
                result = copy.deepcopy(cached[2])
                result["cached"] = True
                result["age_seconds"] = round(time.monotonic() - cached[1], 1)
                return result
            start = time.monotonic()
            result = {"id": server_id, "name": server.name, "host": server.host,
                      "profile": server.profile, "environment": server.environment,
                      "control_mode": server.control_mode, "upstream_id": server.upstream_id,
                      "checked_at": timestamp(), "cached": False, "age_seconds": 0,
                      "health": "unknown", "metrics": None, "services": [], "cores": [], "alerts": []}
            specs = services_for(server)
            try:
                async with self.semaphore:
                    if server.type == "local":
                        data = {"metrics": await asyncio.to_thread(collect_local_metrics), "services": [], "cores": []}
                    else:
                        data = await self.runner(server, {"mode": "snapshot", "services": [s.model_dump() for s in specs]})
                if (not isinstance(data.get("metrics"), dict) or not isinstance(data.get("services"), list)
                        or not isinstance(data.get("cores", []), list)
                        or any(not isinstance(s, dict) for s in data["services"] + data.get("cores", []))):
                    raise ValueError("Invalid snapshot shape")
                result.update(metrics=data["metrics"], cores=data.get("cores", []), health="healthy")
                observations = {s["id"]: s for s in data["services"]}
                for spec in specs:
                    item = {**spec.model_dump(), **observations.get(spec.id, {"state": "unknown"})}
                    item["restart_blocked"] = restart_block_reason(server, spec)
                    result["services"].append(item)
                    if spec.required and not service_healthy(item):
                        result["alerts"].append({"code": "service_not_healthy", "service": spec.id, "state": item["state"]})
                for key in ("ram_percent", "disk_percent"):
                    if (result["metrics"].get(key) or 0) >= 90:
                        result["alerts"].append({"code": "resource_pressure", "metric": key, "value": result["metrics"][key]})
                for core in result["cores"]:
                    if (core.get("fd_count") or 0) >= 8000:
                        result["alerts"].append({"code": "high_core_fd_count", "pid": core["pid"], "value": core["fd_count"]})
                if result["alerts"]:
                    result["health"] = "degraded"
                result["error"] = None
            except (ProbeError, KeyError, TypeError, ValueError, AttributeError) as exc:
                result.update(health="unknown", error=str(exc) if isinstance(exc, ProbeError) else "invalid_probe_response")
                result["alerts"] = [{"code": result["error"]}]
            result["duration_ms"] = round((time.monotonic() - start) * 1000)
            self.cache[server_id] = (identity, time.monotonic(), copy.deepcopy(result))
            return result

    async def summary(self, refresh=False):
        ids = list(self.registry.servers)
        snapshots = await asyncio.gather(*(self.inspect(sid, refresh) for sid in ids), return_exceptions=True)
        nodes = []
        for sid, s in zip(ids, snapshots):
            if isinstance(s, Exception):
                nodes.append({"id": sid, "health": "unknown", "error": "node_changed_during_poll", "alerts": []})
                continue
            nodes.append({k: s[k] for k in ("id", "name", "host", "profile", "environment", "control_mode",
                                          "upstream_id", "health", "checked_at", "cached", "age_seconds", "error", "alerts")})
            nodes[-1]["resources"] = ({k: s["metrics"].get(k) for k in ("cpu_percent", "ram_percent", "disk_percent")}
                                     if s["metrics"] else None)
            nodes[-1]["services"] = {"healthy": sum(service_healthy(v) for v in s["services"]), "total": len(s["services"])}
        return {"schema_version": 1, "generated_at": timestamp(), "cache_ttl_seconds": CACHE_SECONDS,
                "nodes": nodes, "counts": {h: sum(n["health"] == h for n in nodes) for h in ("healthy", "degraded", "unknown")}}

    async def diagnose(self, server_id, group):
        if group not in DIAGNOSTIC_TARGETS:
            raise ValueError("Unknown diagnostic group")
        server = self.registry.get_server(server_id)
        async with self.semaphore:
            result = await self.runner(server, {"mode": "diagnose", "hosts": DIAGNOSTIC_TARGETS[group]})
        return {"schema_version": 1, "server_id": server_id, "group": group, "checked_at": timestamp(),
                "scope": "server_default_egress", "vpn_client_path_verified": False, "checks": result["checks"]}
