"""Local panel lifecycle. Never stops unowned processes or remote servers."""
import argparse
import contextlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, build_opener

import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.paths import data_dir, secret_path
SCRIPT = Path(__file__).resolve()
DATA = data_dir() / "launcher"
STATE = DATA / "state.json"
STOP = DATA / "stop"
URL = "http://127.0.0.1:2077/"
BACKGROUND_FLAGS = sum(getattr(subprocess, flag, 0) for flag in (
    "CREATE_NO_WINDOW", "CREATE_NEW_PROCESS_GROUP", "DETACHED_PROCESS", "CREATE_BREAKAWAY_FROM_JOB",
))


def owned_process(state):
    try:
        process = psutil.Process(state["pid"])
        args = process.cmdline()
        if abs(process.create_time() - state["created"]) > 0.01:
            return None
        if str(SCRIPT) not in args or "serve" not in args or state["instance"] not in args:
            return None
        return process if process.is_running() else None
    except (psutil.Error, KeyError, TypeError):
        return None


def read_state():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def port_busy():
    with socket.socket() as sock:
        return sock.connect_ex(("127.0.0.1", 2077)) == 0


def ready():
    try:
        with build_opener(ProxyHandler({})).open(URL, timeout=1) as response:
            return response.status == 200
    except HTTPError as error:
        return error.code == 401
    except OSError:
        return False


@contextlib.contextmanager
def lifecycle_lock():
    if sys.platform != "win32":
        raise RuntimeError("The background launcher is Windows-only. Use python -m app --open.")
    import msvcrt
    DATA.mkdir(parents=True, exist_ok=True)
    with (DATA / "lock").open("a+b") as lock:
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        deadline = time.monotonic() + 40
        while True:
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise RuntimeError("Another panel command is still running.") from None
                time.sleep(0.2)
        try:
            yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def start():
    if owned_process(read_state()):
        if ready():
            return
        raise RuntimeError("Panel is starting or stopping. Check overseer status.")
    if port_busy():
        raise RuntimeError("Port 2077 belongs to an unmanaged process. Nothing was stopped.")
    STOP.unlink(missing_ok=True)
    STATE.unlink(missing_ok=True)
    instance = uuid.uuid4().hex
    with (DATA / "launcher.log").open("ab") as log:
        child = subprocess.Popen([str(ROOT / "venv" / "Scripts" / "pythonw.exe"), str(SCRIPT), "serve", "--instance", instance],
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=log, creationflags=BACKGROUND_FLAGS)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        state = read_state()
        if state.get("instance") == instance and owned_process(state) and ready():
            return
        if child.poll() is not None:
            raise RuntimeError(f"Panel could not start. Log: {DATA / 'launcher.log'}")
        time.sleep(0.2)
    raise RuntimeError("Startup timed out. Use overseer status or overseer stop.")


def stop():
    state = read_state()
    process = owned_process(state)
    if not process:
        if port_busy():
            raise RuntimeError("Port 2077 belongs to an unmanaged process. Nothing was stopped.")
        print("Overseer: already stopped.")
        return
    STOP.write_text(state["instance"], encoding="ascii")
    try:
        process.wait(timeout=25)
    except psutil.TimeoutExpired:
        raise RuntimeError("Graceful shutdown pending. No processes were force-killed.") from None
    print("Overseer: stopped. Remote servers were not stopped.")


def serve(instance):
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    import uvicorn
    server = uvicorn.Server(uvicorn.Config("app.main:app", host="127.0.0.1", port=2077,
                                         timeout_graceful_shutdown=10, access_log=False))
    current = psutil.Process()
    state = {"pid": current.pid, "created": current.create_time(), "instance": instance}
    temp = STATE.with_suffix(".tmp")
    temp.write_text(json.dumps(state), encoding="utf-8")
    temp.replace(STATE)
    def watch_stop():
        while not server.should_exit:
            try:
                if STOP.read_text(encoding="ascii") == instance:
                    server.should_exit = True
                    return
            except OSError:
                pass
            time.sleep(0.25)
    threading.Thread(target=watch_stop, daemon=True).start()
    try:
        server.run()
    finally:
        if read_state().get("instance") == instance:
            STATE.unlink(missing_ok=True)
            STOP.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="Overseer panel: open, start, stop, restart, status")
    parser.add_argument("action", nargs="?", default="open", choices=["open", "start", "stop", "restart", "status", "serve"])
    parser.add_argument("--instance", help=argparse.SUPPRESS)
    parser.add_argument("--notify", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if args.action == "serve":
            if not args.instance:
                raise RuntimeError("Use overseer start.")
            serve(args.instance)
            return 0
        with lifecycle_lock():
            if args.action == "status":
                process = owned_process(read_state())
                print("Overseer: " + ("running " + URL if process and ready() else
                    "starting/stopping" if process else "unmanaged listener on port 2077" if port_busy() else "stopped"))
            elif args.action == "stop":
                stop()
            else:
                if args.action == "restart":
                    stop()
                start()
                print("Overseer: running " + URL)
                if args.action == "open":
                    secret = json.loads(secret_path().read_text(encoding="utf-8"))
                    webbrowser.open(URL + "?" + urlencode({"access_token": secret["access_token"]}))
        return 0
    except Exception as error:
        print("Overseer: " + str(error), file=sys.stderr)
        if args.notify:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, str(error), "Overseer Network", 0x10)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
