"""Durable action receipts: a lost response must never trigger a blind retry."""

import asyncio
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from app.fleet import fingerprint, service_healthy, timestamp
from app.fleet_profiles import restart_block_reason, services_for


class OperationError(Exception):
    def __init__(self, code, status=409):
        self.code, self.status = code, status
        super().__init__(code)


def service_identity(item):
    return {k: item.get(k) for k in ("state", "pid", "start_ticks", "started_at", "restarts")}


class Operations:
    def __init__(self, fleet, path):
        self.fleet = fleet
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS operations (
                id TEXT PRIMARY KEY, node TEXT NOT NULL, service TEXT NOT NULL,
                state TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
                payload TEXT NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS operations_target ON operations(node, service, state)")

    @contextmanager
    def db(self):
        conn = sqlite3.connect(self.path, timeout=5)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def save(self, record):
        with self.db() as db:
            db.execute("INSERT INTO operations VALUES (?,?,?,?,?,?,?)",
                (record["id"], record["server_id"], record["service_id"], record["state"],
                 time.time(), time.time(), json.dumps(record)))

    def get(self, operation_id):
        with self.db() as db:
            row = db.execute("SELECT state,updated,payload FROM operations WHERE id=?", (operation_id,)).fetchone()
        if not row:
            raise OperationError("operation_not_found", 404)
        record = json.loads(row[2])
        record["state"] = row[0]
        if row[0] == "executing" and time.time() - row[1] > 120:
            record["state"] = "unknown"
            record["error"] = "controller_interrupted_manual_inspection_required"
        return record

    def history(self, limit=20):
        with self.db() as db:
            ids = db.execute("SELECT id FROM operations ORDER BY created DESC LIMIT ?", (limit,)).fetchall()
        return [self.get(row[0]) for row in ids]

    def resolve_target(self, server_id, service_id):
        server = self.fleet.registry.get_server(server_id)
        spec = next((s for s in services_for(server) if s.id == service_id), None)
        if spec is None:
            raise OperationError("service_not_found", 404)
        reason = restart_block_reason(server, spec)
        if reason:
            raise OperationError(reason, 403)
        return server, spec

    async def plan(self, server_id, service_id):
        server, spec = self.resolve_target(server_id, service_id)
        snapshot = await self.fleet.inspect(server_id, refresh=True)
        item = next((s for s in snapshot["services"] if s["id"] == service_id), None)
        if not item or not service_healthy(item):
            raise OperationError("service_not_running_or_unverified")
        with self.db() as db:
            pending = db.execute("SELECT 1 FROM operations WHERE node=? AND service=? AND state IN ('executing','unknown')",
                                 (server_id, service_id)).fetchone()
        if pending:
            raise OperationError("unresolved_previous_operation")
        plan = {"id": str(uuid.uuid4()), "server_id": server_id, "service_id": service_id,
                "action": "restart", "state": "planned", "created_at": timestamp(),
                "expires_at_epoch": time.time() + 120, "target": spec.target, "kind": spec.kind,
                "configuration_fingerprint": fingerprint(server), "before": service_identity(item),
                "impact": "Connections handled by this service can be interrupted."}
        self.save(plan)
        return plan

    def finish(self, record, state, **fields):
        previous = record["state"]
        record = {**record, **fields, "state": state, "completed_at": timestamp()}
        with self.db() as db:
            changed = db.execute("UPDATE operations SET state=?,updated=?,payload=? WHERE id=? AND state=?",
                       (state, time.time(), json.dumps(record), record["id"], previous)).rowcount
        if not changed:
            return self.get(record["id"])
        return record

    async def execute(self, operation_id, confirmation):
        record = self.get(operation_id)
        if confirmation != operation_id:
            raise OperationError("confirmation_mismatch", 400)
        if record["state"] != "planned":
            return record
        if record["expires_at_epoch"] < time.time():
            return self.finish(record, "expired")
        server, spec = self.resolve_target(record["server_id"], record["service_id"])
        if record["expires_at_epoch"] < time.time():
            return self.finish(record, "expired")
        if fingerprint(server) != record["configuration_fingerprint"]:
            return self.finish(record, "rejected", error="configuration_changed")
        snapshot = await self.fleet.inspect(record["server_id"], refresh=True)
        item = next((s for s in snapshot["services"] if s["id"] == spec.id), None)
        if not item or service_identity(item) != record["before"]:
            return self.finish(record, "rejected", error="service_changed")
        # There must be no await between the final registry check and durable claim.
        server, spec = self.resolve_target(record["server_id"], record["service_id"])
        if fingerprint(server) != record["configuration_fingerprint"]:
            return self.finish(record, "rejected", error="configuration_changed")
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            state = db.execute("SELECT state FROM operations WHERE id=?", (operation_id,)).fetchone()[0]
            if state != "planned":
                return self.get(operation_id)
            if db.execute("SELECT 1 FROM operations WHERE node=? AND service=? AND state IN ('executing','unknown')",
                          (record["server_id"], spec.id)).fetchone():
                raise OperationError("unresolved_previous_operation")
            claimed = db.execute("UPDATE operations SET state='executing',updated=? WHERE id=? AND state='planned'",
                                 (time.time(), operation_id)).rowcount
        if not claimed:
            return self.get(operation_id)
        record["state"] = "executing"
        try:
            async with self.fleet.semaphore:
                current = self.fleet.registry.get_server(record["server_id"])
                if fingerprint(current) != record["configuration_fingerprint"]:
                    return self.finish(record, "rejected", error="configuration_changed")
                if record["expires_at_epoch"] < time.time():
                    return self.finish(record, "expired")
                receipt = await self.fleet.runner(server, {"mode": "restart", "services": [spec.model_dump()],
                                                          "expected": record["before"]})
                if receipt.get("error") == "service_changed":
                    return self.finish(record, "rejected", error="service_changed")
            self.fleet.invalidate(record["server_id"])
            after = await self.fleet.inspect(record["server_id"], refresh=True)
            item = next((s for s in after["services"] if s["id"] == spec.id), None)
            changed = item and service_identity(item) != record["before"]
            if receipt.get("accepted") and item and service_healthy(item) and changed:
                return self.finish(record, "succeeded", after=service_identity(item))
            return self.finish(record, "unknown", error="restart_not_verified", after=service_identity(item or {}))
        except (Exception, asyncio.CancelledError):
            # An SSH timeout cannot tell us whether systemd already applied the action.
            return self.finish(record, "unknown", error="execution_interrupted_manual_inspection_required")

    async def reconcile(self, operation_id):
        record = self.get(operation_id)
        if record["state"] != "unknown":
            raise OperationError("only_unknown_operation_can_be_reconciled")
        node = self.fleet.registry.get_server(record["server_id"])
        if fingerprint(node) != record["configuration_fingerprint"]:
            raise OperationError("configuration_changed")
        snapshot = await self.fleet.inspect(record["server_id"], refresh=True)
        item = next((s for s in snapshot["services"] if s["id"] == record["service_id"]), None)
        if not item or not service_healthy(item):
            raise OperationError("service_not_running_or_unverified")
        # This records present health, not proof that the earlier command succeeded.
        with self.db() as db:
            db.execute("UPDATE operations SET state='unknown' WHERE id=? AND state='executing' AND updated<?",
                       (operation_id, time.time() - 120))
        return self.finish(record, "reconciled", after=service_identity(item), execution_confirmed=False)
