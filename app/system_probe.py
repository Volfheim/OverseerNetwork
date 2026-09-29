import asyncio
import csv
import math
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

import psutil

def collect_gpu_metrics() -> Optional[Dict[str, Any]]:
    executable = shutil.which("nvidia-smi")
    if not executable and sys.platform == "win32":
        fallback = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "NVIDIA Corporation/NVSMI/nvidia-smi.exe"
        if fallback.is_file():
            executable = str(fallback)
    if not executable:
        return None
    try:
        # A windowless parent does not prevent console children from opening windows.
        result = subprocess.run(
            [executable, "--query-gpu=name,utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=3,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        if result.returncode:
            return None
        rows = csv.reader(result.stdout.splitlines(), skipinitialspace=True)
        name, load, used, total = next(rows)
        load, used, total = float(load), float(used), float(total)
        if not name.strip() or not all(math.isfinite(value) for value in (load, used, total)):
            return None
        if not (0 <= load <= 100 and 0 <= used <= total and total > 0):
            return None
        return {"name": name.strip(), "load_percent": round(load, 1),
                "vram_used_mb": round(used, 0), "vram_total_mb": round(total, 0)}
    except (OSError, subprocess.TimeoutExpired, ValueError, StopIteration, csv.Error):
        return None


def collect_local_metrics() -> Dict[str, Any]:
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    cpu_name = platform.processor() or platform.machine() or "UNKNOWN CPU"
    metrics: Dict[str, Any] = {
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "cpu_name": cpu_name,
        "cpu_percent": psutil.cpu_percent(interval=None),
        "ram_percent": mem.percent,
        "ram_used_gb": round(mem.used / (1024**3), 2),
        "ram_total_gb": round(mem.total / (1024**3), 2),
        "disk_percent": disk.percent,
        "disk_used_gb": round(disk.used / (1024**3), 2),
        "disk_total_gb": round(disk.total / (1024**3), 2),
        "gpu": collect_gpu_metrics(),
    }

    return metrics


async def check_tcp(host: Optional[str], port: int, timeout: float = 3.0) -> Dict[str, Any]:
    if not host:
        return {"online": False, "latency_ms": None, "error": "host is empty"}

    start = time.perf_counter()
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=timeout,
        )
        writer.close()
        await writer.wait_closed()
        latency_ms = round((time.perf_counter() - start) * 1000)
        return {"online": True, "latency_ms": latency_ms, "error": None}
    except Exception as exc:
        return {"online": False, "latency_ms": None, "error": str(exc)}
