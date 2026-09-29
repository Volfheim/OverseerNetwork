"""Stdlib-only probe streamed over SSH. No installation or secret/config reads."""

import concurrent.futures
import http.client
import json
import os
import platform
import re
import shutil
import socket
import ssl
import subprocess
import sys
import time
from pathlib import Path


TARGET_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.@-]{0,127}$")


def command(args, timeout=5):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return result.returncode, result.stdout[:100000]
    except (OSError, subprocess.TimeoutExpired):
        return -1, ""


def process_identity(pid):
    try:
        root = Path("/proc") / str(int(pid))
        stat = (root / "stat").read_text().rsplit(")", 1)[1].split()
        with os.scandir(root / "fd") as entries:
            fd_count = sum(1 for _ in entries)
        return {"pid": int(pid), "start_ticks": int(stat[19]), "fd_count": fd_count}
    except (OSError, ValueError, IndexError):
        return {"pid": int(pid or 0), "start_ticks": None, "fd_count": None}


def metrics():
    def cpu():
        nums = [int(v) for v in Path("/proc/stat").read_text().splitlines()[0].split()[1:9]]
        return sum(nums), nums[3] + nums[4]
    a, idle_a = cpu()
    time.sleep(0.15)
    b, idle_b = cpu()
    mem = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, value = line.split(":", 1)
        mem[key] = int(value.split()[0])
    total, available = mem["MemTotal"], mem.get("MemAvailable", mem["MemFree"])
    disk = shutil.disk_usage("/")
    return {
        "hostname": socket.gethostname(), "platform": platform.system() + " " + platform.release(),
        "cpu_percent": round(100 * (1 - (idle_b - idle_a) / max(1, b - a)), 1),
        "cpu_count": os.cpu_count(), "load_1m": os.getloadavg()[0],
        "ram_percent": round(100 * (total - available) / total, 1),
        "ram_total_gb": round(total / 1024**2, 2), "ram_used_gb": round((total - available) / 1024**2, 2),
        "disk_percent": round(100 * disk.used / disk.total, 1),
        "disk_total_gb": round(disk.total / 1024**3, 2), "disk_used_gb": round(disk.used / 1024**3, 2),
        "uptime_seconds": int(float(Path("/proc/uptime").read_text().split()[0])),
    }


def inspect_services(specs):
    results = {}
    units = [s["target"] for s in specs if s["kind"] == "systemd"]
    if units:
        rc, raw = command(["systemctl", "show", *units, "--no-pager",
                           "--property=Id,LoadState,ActiveState,SubState,MainPID,NRestarts,ActiveEnterTimestampMonotonic"])
        by_id = {}
        for block in raw.strip().split("\n\n"):
            fields = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
            by_id[fields.get("Id")] = fields
        for spec in (s for s in specs if s["kind"] == "systemd"):
            f = by_id.get(spec["target"], {})
            state = f.get("ActiveState", "unknown")
            if f.get("LoadState") == "not-found":
                state = "not_found"
            identity = process_identity(f.get("MainPID", "0"))
            results[spec["id"]] = dict(state=state, substate=f.get("SubState"),
                restarts=int(f.get("NRestarts", "0")), **identity,
                started_at=f.get("ActiveEnterTimestampMonotonic"),
                error="systemd_unavailable" if rc != 0 and not f else None)
    containers = [s["target"] for s in specs if s["kind"] == "container"]
    if containers:
        # Docker's full inspect contains secrets. Select only operational fields on-host.
        fmt = '{"name":{{json .Name}},"state":{{json .State.Status}},"pid":{{.State.Pid}},"restarts":{{.RestartCount}},"started_at":{{json .State.StartedAt}},"health":{{if .State.Health}}{{json .State.Health.Status}}{{else}}null{{end}}}'
        rc, raw = command(["docker", "inspect", "--format", fmt, *containers])
        by_name = {}
        for line in raw.splitlines():
            try:
                value = json.loads(line)
                by_name[value["name"].lstrip("/")] = value
            except (ValueError, KeyError):
                pass
        for spec in (s for s in specs if s["kind"] == "container"):
            value = by_name.get(spec["target"])
            if value:
                results[spec["id"]] = {**value, **process_identity(value["pid"]), "error": None}
            else:
                results[spec["id"]] = {"state": "unknown", "error": "container_unavailable"}
    return [{"id": s["id"], **results[s["id"]]} for s in specs]


def cores():
    result = []
    for root in Path("/proc").iterdir():
        if not root.name.isdigit():
            continue
        try:
            name = (root / "comm").read_text().strip()
            if name in {"xray", "xray-linux-amd6", "xray-linux-amd64", "rw-core"} or name.startswith("xray-linux-"):
                result.append({"name": name, **process_identity(root.name)})
        except OSError:
            continue
        if len(result) >= 32:
            break
    return result


def diagnose_host(host):
    start = time.monotonic()
    result = {"host": host, "scope": "server_default_egress", "dns": [], "http_status": None, "peer_ip": None,
              "tls": None, "outcome": "unknown"}
    try:
        result["dns"] = sorted({v[4][0] for v in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})[:16]
    except OSError:
        result.update(outcome="dns_error", elapsed_ms=round((time.monotonic() - start) * 1000))
        return result
    conn = http.client.HTTPSConnection(host, timeout=4, context=ssl.create_default_context())
    try:
        conn.connect()
        result["tls"] = conn.sock.version()
        result["peer_ip"] = conn.sock.getpeername()[0]
        conn.request("HEAD", "/", headers={"User-Agent": "Overseer-ReadOnly-Probe/1.0"})
        response = conn.getresponse()
        result["http_status"] = response.status
        result["outcome"] = ("challenge" if response.getheader("cf-mitigated") == "challenge" else
                             "http_error" if response.status >= 400 else "reachable")
    except ssl.SSLError:
        result["outcome"] = "tls_error"
    except (socket.timeout, TimeoutError):
        result["outcome"] = "timeout"
    except (OSError, http.client.HTTPException):
        result["outcome"] = "connection_error"
    finally:
        conn.close()
    result["elapsed_ms"] = round((time.monotonic() - start) * 1000)
    return result


def run(payload):
    mode = payload.get("mode", "snapshot")
    specs = payload.get("services", [])
    if len(specs) > 64 or any(not TARGET_PATTERN.fullmatch(s["target"]) or
                             s["kind"] not in {"systemd", "container"} for s in specs):
        raise ValueError("Invalid service specification")
    if mode == "snapshot":
        return {"metrics": metrics(), "services": inspect_services(specs), "cores": cores()}
    if mode == "diagnose":
        hosts = payload["hosts"]
        if not 1 <= len(hosts) <= 6 or any(not re.fullmatch(r"[a-zA-Z0-9.-]{1,253}", h) for h in hosts):
            raise ValueError("Invalid diagnostic targets")
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            return {"checks": list(pool.map(diagnose_host, hosts))}
    if mode == "restart" and len(specs) == 1:
        spec = specs[0]
        current = inspect_services(specs)[0]
        expected = payload.get("expected")
        identity = {k: current.get(k) for k in ("state", "pid", "start_ticks", "started_at", "restarts")}
        if not expected or identity != expected:
            return {"accepted": False, "error": "service_changed"}
        args = (["systemctl", "restart", spec["target"]] if spec["kind"] == "systemd" else
                ["docker", "restart", "--time", "15", spec["target"]])
        rc, _ = command(args, timeout=22)
        return {"accepted": rc == 0}
    raise ValueError("Unsupported probe")


if __name__ == "__main__":
    print(json.dumps(run(json.loads(sys.argv[1])), separators=(",", ":")))
