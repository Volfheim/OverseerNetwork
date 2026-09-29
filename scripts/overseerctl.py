"""Compact, authenticated client for humans and local coding agents (stdlib only)."""

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.paths import secret_path as default_secret_path


def request(base, path, data=None, secret_path=None):
    parsed = urllib.parse.urlsplit(base)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Use an HTTP(S) server URL without credentials or query")
    if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Remote connections require HTTPS")
    token = json.loads(Path(secret_path or default_secret_path()).read_text(encoding="utf-8"))["access_token"]
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(base.rstrip("/") + path, data=body,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    # Never forward the bearer credential through a redirect or an ambient proxy.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(req, timeout=90) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:2077")
    parser.add_argument("--secret-file", type=Path)
    parser.add_argument("--pretty", action="store_true")
    commands = parser.add_subparsers(dest="command", required=True)
    summary = commands.add_parser("summary", help="Compact fleet health and actionable alerts")
    summary.add_argument("--refresh", action="store_true")
    node = commands.add_parser("inspect", help="Node metrics, services, core PIDs and FD counts")
    node.add_argument("node")
    node.add_argument("--refresh", action="store_true")
    diag = commands.add_parser("diagnose", help="DNS and TLS/HTTP from the selected node")
    diag.add_argument("node")
    diag.add_argument("group", choices=["openai", "google", "youtube", "russian"])
    for name in ("capabilities", "inventory", "history"):
        commands.add_parser(name)
    plan = commands.add_parser("plan", help="Review a restart of an explicitly enabled non-production service")
    plan.add_argument("node")
    plan.add_argument("service")
    execute = commands.add_parser("execute", help="Execute the exact reviewed plan, once")
    execute.add_argument("operation")
    execute.add_argument("--confirm", required=True, help="Repeat the reviewed operation ID")
    status = commands.add_parser("operation", help="Read the receipt, especially after a lost response")
    status.add_argument("operation")
    reconcile = commands.add_parser("reconcile", help="Recheck an unknown operation without repeating its action")
    reconcile.add_argument("operation")
    args = parser.parse_args()
    encode = lambda value: urllib.parse.quote(value, safe="")
    body = None
    if args.command == "summary":
        path = "/api/v1/fleet?refresh=" + str(args.refresh).lower()
    elif args.command == "inspect":
        path = "/api/v1/nodes/" + encode(args.node) + "?refresh=" + str(args.refresh).lower()
    elif args.command == "diagnose":
        path, body = "/api/v1/nodes/" + encode(args.node) + "/diagnostics", {"group": args.group}
    elif args.command == "plan":
        path, body = "/api/v1/nodes/" + encode(args.node) + "/actions/plan", {"service_id": args.service}
    elif args.command == "execute":
        path, body = "/api/v1/operations/" + encode(args.operation) + "/execute", {"confirm": args.confirm}
    elif args.command == "operation":
        path = "/api/v1/operations/" + encode(args.operation)
    elif args.command == "reconcile":
        path, body = "/api/v1/operations/" + encode(args.operation) + "/reconcile", {}
    else:
        path = {"inventory": "/api/servers", "capabilities": "/api/v1/capabilities", "history": "/api/v1/operations"}[args.command]
    try:
        result = request(args.url, path, body, args.secret_file)
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read(4096)).get("detail", "request_failed")
        except ValueError:
            detail = "request_failed"
        print(json.dumps({"error": detail, "http_status": exc.code}))
        return 1
    except (OSError, ValueError, KeyError):
        print(json.dumps({"error": "connection_or_local_configuration_failed"}))
        return 1
    print(json.dumps(result, ensure_ascii=True, indent=2 if args.pretty else None,
                     separators=None if args.pretty else (",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
