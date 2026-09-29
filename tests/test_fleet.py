import asyncio
import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import AppConfig, ManagedService, ServerConfig
from app.fleet import FleetManager
from app.fleet_profiles import restart_block_reason, services_for
from app.operations import OperationError, Operations
from app.remote_probe import run as remote_run, diagnose_host
from app.ssh_transport import ProbeError


def server(**extra):
    return ServerConfig(name="Fixture", type="ssh", host="fixture.invalid", username="tester",
        key_path="~/.ssh/test-not-a-real-key", auto_geo=False, **extra)


class FakeRunner:
    def __init__(self):
        self.calls = []
        self.pid = 100
        self.state = "active"
        self.error = None
        self.restart_error = None

    async def __call__(self, node, payload):
        self.calls.append(copy.deepcopy(payload))
        await asyncio.sleep(0)
        if self.error:
            raise ProbeError(self.error)
        if payload["mode"] == "restart":
            self.pid += 1
            if self.restart_error:
                raise ProbeError(self.restart_error)
            return {"accepted": True}
        if payload["mode"] == "diagnose":
            return {"checks": [{"host": h, "outcome": "challenge", "dns": ["192.0.2.1"], "http_status": 403}
                               for h in payload["hosts"]]}
        return {"metrics": {"cpu_percent": 12, "ram_percent": 30, "disk_percent": 40}, "cores": [],
            "services": [{"id": s["id"], "state": self.state, "pid": self.pid, "start_ticks": self.pid,
                          "started_at": str(self.pid), "restarts": self.pid - 100} for s in payload["services"]]}


class Fixture:
    def setup_fixture(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.registry_path = self.path / "servers.yaml"
        self.registry_path.write_text("servers: {}\n", encoding="utf-8")
        self.registry = AppConfig(self.registry_path)
        self.custom = ManagedService(id="worker", label="Fixture worker", target="fixture-worker.service", allow_restart=True)
        self.node = server(services=[self.custom], environment="lab", control_mode="operate")
        self.registry.upsert_server("fixture", self.node.storage_dict())
        self.runner = FakeRunner()
        self.fleet = FleetManager(self.registry, runner=self.runner)
        self.operations = Operations(self.fleet, self.path / "operations.sqlite3")


class ConfigTests(Fixture, unittest.TestCase):
    def setUp(self):
        self.setup_fixture()

    def test_registry_write_failure_preserves_in_memory_and_disk(self):
        before = self.registry_path.read_bytes()
        with patch("app.config.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.registry.upsert_server("fixture", {**self.node.storage_dict(), "name": "Changed"})
        self.assertEqual(self.registry.servers["fixture"].name, "Fixture")
        self.assertEqual(self.registry_path.read_bytes(), before)

    def test_invalid_service_target_cannot_become_shell_command(self):
        for target in ["a;id", "$(id)", "--all", "../sshd", "a b"]:
            with self.subTest(target=target), self.assertRaises(ValidationError):
                ManagedService(id="test", label="test", target=target)

    def test_ssh_requires_valid_connection(self):
        for payload in [{"port": 0}, {"port": 65536}, {"username": ""}, {"host": ""}]:
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                ServerConfig(**{**self.node.storage_dict(), **payload})

    def test_public_inventory_hides_key_path(self):
        public = json.dumps(self.registry.list_public_servers())
        self.assertNotIn("key_path", public)
        self.assertNotIn("test-not-a-real-key", public)

    def test_alias_targets_cannot_create_two_independent_restart_locks(self):
        other = ManagedService(id="alias", label="Alias", target="fixture-worker", allow_restart=True)
        with self.assertRaises(ValidationError):
            server(services=[self.custom, other])

    def test_topology_rejects_dangling_and_cycle_and_referenced_delete(self):
        for parent in ("missing", "fixture"):
            with self.assertRaises(ValueError):
                self.registry.upsert_server("fixture", {**self.node.storage_dict(), "upstream_id": parent})
        self.registry.upsert_server("child", {**self.node.storage_dict(), "upstream_id": "fixture"})
        with self.assertRaises(ValueError):
            self.registry.upsert_server("fixture", {**self.node.storage_dict(), "upstream_id": "child"})
        with self.assertRaises(ValueError):
            self.registry.delete_server("fixture")

    def test_protected_workload_cannot_restart_custom_service(self):
        node = server(protected=True, services=[ManagedService(id="legacy", label="Rename", target="other", allow_restart=True)])
        legacy = next(s for s in services_for(node) if s.id == "legacy")
        self.assertEqual(legacy.target, "other.service")
        self.assertEqual(restart_block_reason(node, legacy), "protected_workload")

    def test_custom_alias_cannot_unlock_protected_service(self):
        self.node.protected_targets = ["database.service"]
        for target in ("database", "database.service", "sshd", "docker"):
            item = self.custom.model_copy(update={"target": target})
            self.assertEqual(restart_block_reason(self.node, item), "protected_service")


class FleetTests(Fixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.setup_fixture()

    async def test_summary_is_compact_and_cached_without_secret(self):
        first = await self.fleet.summary()
        second = await self.fleet.summary()
        self.assertEqual(len(self.runner.calls), 1)
        self.assertEqual(first["counts"]["healthy"], 1)
        self.assertTrue(second["nodes"][0]["cached"])
        self.assertNotIn("target", json.dumps(second))
        self.assertNotIn("test-not-a-real-key", json.dumps(second))

    async def test_concurrent_reads_share_cached_measurement(self):
        await asyncio.gather(*(self.fleet.inspect("fixture") for _ in range(8)))
        self.assertEqual(len(self.runner.calls), 1)

    async def test_concurrent_refreshes_share_new_measurement(self):
        await asyncio.gather(*(self.fleet.inspect("fixture", refresh=True) for _ in range(8)))
        self.assertEqual(len(self.runner.calls), 1)

    async def test_config_change_invalidates_cache(self):
        await self.fleet.inspect("fixture")
        self.registry.upsert_server("fixture", {**self.node.storage_dict(), "host": "other.invalid"})
        await self.fleet.inspect("fixture")
        self.assertEqual(len(self.runner.calls), 2)

    async def test_refresh_observes_failed_service(self):
        await self.fleet.inspect("fixture")
        self.runner.state = "failed"
        result = await self.fleet.inspect("fixture", refresh=True)
        self.assertEqual(result["health"], "degraded")
        self.assertEqual(result["alerts"][0]["service"], "worker")

    async def test_auth_failure_is_unknown_not_healthy_or_offline(self):
        self.runner.error = "ssh_auth_failed"
        result = await self.fleet.inspect("fixture")
        self.assertEqual(result["health"], "unknown")
        self.assertIsNone(result["metrics"])

    async def test_missing_observation_does_not_look_healthy(self):
        async def incomplete(node, payload):
            return {"services": [], "metrics": {}, "cores": []}
        self.fleet.runner = incomplete
        result = await self.fleet.inspect("fixture")
        self.assertEqual(result["health"], "degraded")

    async def test_malformed_probe_cannot_report_success(self):
        async def malformed(node, payload):
            return {"metrics": None, "services": [], "cores": []}
        self.fleet.runner = malformed
        result = await self.fleet.inspect("fixture")
        self.assertEqual(result["health"], "unknown")
        self.assertEqual(result["error"], "invalid_probe_response")

    async def test_diagnostics_distinguish_cloudflare_and_client_path(self):
        result = await self.fleet.diagnose("fixture", "openai")
        self.assertEqual(result["checks"][0]["outcome"], "challenge")
        self.assertFalse(result["vpn_client_path_verified"])
        with self.assertRaises(ValueError):
            await self.fleet.diagnose("fixture", "http://internal.invalid")


class OperationTests(Fixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.setup_fixture()

    async def plan(self):
        return await self.operations.plan("fixture", "worker")

    async def test_plan_never_restarts_and_requires_explicit_matching_confirmation(self):
        plan = await self.plan()
        self.assertEqual([c["mode"] for c in self.runner.calls], ["snapshot"])
        with self.assertRaises(OperationError):
            await self.operations.execute(plan["id"], "yes")
        self.assertEqual(self.runner.pid, 100)

    async def test_success_receipt_survives_recreation_and_repeat(self):
        plan = await self.plan()
        result = await self.operations.execute(plan["id"], plan["id"])
        self.assertEqual(result["state"], "succeeded")
        another = Operations(self.fleet, self.path / "operations.sqlite3")
        repeat = await another.execute(plan["id"], plan["id"])
        self.assertEqual(repeat, result)
        self.assertEqual(self.runner.pid, 101)

    async def test_lost_ssh_response_is_unknown_and_not_retried(self):
        plan = await self.plan()
        self.runner.restart_error = "ssh_unavailable"
        result = await self.operations.execute(plan["id"], plan["id"])
        self.assertEqual(result["state"], "unknown")
        await self.operations.execute(plan["id"], plan["id"])
        self.assertEqual(self.runner.pid, 101)
        with self.assertRaises(OperationError):
            await self.plan()

    async def test_changed_config_rejects_plan(self):
        plan = await self.plan()
        self.registry.upsert_server("fixture", {**self.node.storage_dict(), "host": "other.invalid"})
        result = await self.operations.execute(plan["id"], plan["id"])
        self.assertEqual(result["state"], "rejected")
        self.assertEqual(self.runner.pid, 100)

    async def test_reconciliation_observes_health_without_claiming_restart_succeeded(self):
        plan = await self.plan()
        self.runner.restart_error = "ssh_unavailable"
        await self.operations.execute(plan["id"], plan["id"])
        result = await self.operations.reconcile(plan["id"])
        self.assertEqual(result["state"], "reconciled")
        self.assertFalse(result["execution_confirmed"])
        self.assertEqual(self.runner.pid, 101)
        new_plan = await self.plan()
        self.assertEqual(new_plan["before"]["pid"], 101)

    async def test_changed_process_rejects_plan(self):
        plan = await self.plan()
        self.runner.pid += 1
        result = await self.operations.execute(plan["id"], plan["id"])
        self.assertEqual(result["error"], "service_changed")
        self.assertFalse(any(c["mode"] == "restart" for c in self.runner.calls))

    async def test_expired_plan_cannot_execute(self):
        plan = await self.plan()
        with patch("app.operations.time.time", return_value=time.time() + 200):
            result = await self.operations.execute(plan["id"], plan["id"])
        self.assertEqual(result["state"], "expired")

    async def test_concurrent_execute_never_restarts_twice(self):
        plan = await self.plan()
        await asyncio.gather(*(self.operations.execute(plan["id"], plan["id"]) for _ in range(4)), return_exceptions=True)
        self.assertEqual(self.runner.pid, 101)
        self.assertEqual(self.operations.get(plan["id"])["state"], "succeeded")

    async def test_production_restart_denied_before_remote_io(self):
        self.registry.upsert_server("fixture", {**self.node.storage_dict(), "environment": "production"})
        with self.assertRaises(OperationError) as error:
            await self.plan()
        self.assertEqual(error.exception.status, 403)
        self.assertEqual(self.runner.calls, [])


class ApiTests(Fixture, unittest.TestCase):
    def setUp(self):
        self.setup_fixture()
        from app import main, fleet_api
        self.main = main
        for module, name, value in [(main, "config", self.registry), (main, "fleet", self.fleet),
                (fleet_api, "config", self.registry), (fleet_api, "fleet", self.fleet),
                (fleet_api, "operations", self.operations), (main.access_control, "access_token", "fixture-token")]:
            p = patch.object(module, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)
        self.headers = {"Authorization": "Bearer fixture-token"}

    def test_every_machine_endpoint_requires_auth(self):
        for method, path, body in [
            ("get", "/api/v1/fleet", None), ("get", "/api/v1/capabilities", None),
            ("get", "/api/v1/nodes/fixture", None), ("get", "/api/v1/operations", None),
            ("get", "/api/geo/nodes", None), ("post", "/api/geo/lookup", {"host": "8.8.8.8"}),
            ("post", "/api/v1/nodes/fixture/diagnostics", {"group": "openai"}),
            ("post", "/api/v1/nodes/fixture/actions/plan", {"service_id": "worker"}),
            ("post", "/api/v1/operations/x/execute", {"confirm": "x"}),
        ]:
            with self.subTest(path=path):
                kwargs = {"json": body} if body is not None else {}
                self.assertEqual(getattr(self.client, method)(path, **kwargs).status_code, 401)

    def test_api_workflow_plan_execute_repeat(self):
        response = self.client.post("/api/v1/nodes/fixture/actions/plan", headers=self.headers, json={"service_id": "worker"})
        self.assertEqual(response.status_code, 200)
        operation_id = response.json()["id"]
        for _ in range(2):
            response = self.client.post(f"/api/v1/operations/{operation_id}/execute", headers=self.headers, json={"confirm": operation_id})
            self.assertEqual(response.json()["state"], "succeeded")
        self.assertEqual(self.runner.pid, 101)

    def test_geo_api_uses_server_ip_and_preserves_connection_and_configuration(self):
        from app.geolocation import GeoLocator
        calls = []
        def lookup(host):
            calls.append(host)
            return {"ip": "8.8.8.8", "coordinates": [40, -70], "city": "Fixture geo"}
        self.registry.upsert_server("fixture", {**self.node.storage_dict(), "auto_geo": True, "coordinates": [55, 37]})
        before = self.registry_path.read_bytes()
        with patch.object(self.main, "geolocator", GeoLocator(lookup=lookup)):
            response = self.client.get("/api/geo/nodes", headers=self.headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["locations"]["fixture"]["coordinates"], [40, -70])
            nodes = self.client.get("/api/servers", headers=self.headers).json()["servers"]
            self.assertEqual(nodes[0]["coordinates"], [40, -70])
            self.assertEqual(nodes[0]["host"], self.node.host)
        self.assertEqual(calls, [self.node.host])
        self.assertEqual(self.registry_path.read_bytes(), before)

    def test_pc_physical_location_survives_save_and_reload(self):
        payload = {"id": "pc", "name": "PC", "type": "local", "physical_location": [51.5, -0.12], "city": "London"}
        response = self.client.post("/api/servers", headers=self.headers, json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(AppConfig(self.registry_path).get_server("pc").physical_location, [51.5, -0.12])
        nodes = self.client.get("/api/servers", headers=self.headers).json()["servers"]
        pc = next(node for node in nodes if node["id"] == "pc")
        self.assertEqual(pc["coordinates"], [51.5, -0.12])
        self.assertEqual(pc["geo"]["source"], "physical")

    def test_invalid_diagnostic_and_shell_action_are_rejected(self):
        for route, data in [("diagnostics", {"group": "localhost"}),
                            ("actions/plan", {"service_id": "worker", "action": "exec", "command": "id"})]:
            response = self.client.post("/api/v1/nodes/fixture/" + route, headers=self.headers, json=data)
            self.assertEqual(response.status_code, 422)
        self.assertEqual(self.runner.calls, [])

    def test_duplicate_create_cannot_overwrite_registry(self):
        response = self.client.post("/api/servers", headers=self.headers, json={"id": "fixture", "name": "Oops"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.registry.get_server("fixture").name, "Fixture")

    def test_create_and_reload_preserves_custom_service_and_profile(self):
        payload = {**self.node.storage_dict(), "id": "created"}
        response = self.client.post("/api/servers", headers=self.headers, json=payload)
        self.assertEqual(response.status_code, 200)
        reloaded = AppConfig(self.registry_path).get_server("created")
        self.assertEqual(reloaded.services, self.node.services)
        self.assertEqual(reloaded.control_mode, "operate")

    def test_old_editor_payload_preserves_management_settings(self):
        response = self.client.put("/api/servers/fixture", headers=self.headers, json={"id": "fixture", "name": "Renamed", "key_path": ""})
        self.assertEqual(response.status_code, 200)
        saved = self.registry.get_server("fixture")
        self.assertEqual(saved.control_mode, "operate")
        self.assertEqual(saved.services, self.node.services)
        self.assertEqual(saved.key_path, self.node.key_path)

    def test_unknown_node_does_not_probe_or_create(self):
        self.assertEqual(self.client.get("/api/v1/nodes/missing", headers=self.headers).status_code, 404)
        self.assertEqual(self.client.put("/api/servers/missing", headers=self.headers, json={"name": "Oops"}).status_code, 404)
        self.assertEqual(self.runner.calls, [])


class RemoteProbeTests(unittest.TestCase):
    def test_remote_input_rejects_shell_metacharacters(self):
        with self.assertRaises(ValueError):
            remote_run({"mode": "restart", "services": [{"target": "a;id", "kind": "systemd"}]})

    def test_cloudflare_403_is_not_diagnosed_as_network_block(self):
        with patch("app.remote_probe.socket.getaddrinfo", return_value=[(None, None, None, None, ("192.0.2.1", 443))]), \
             patch("app.remote_probe.http.client.HTTPSConnection") as conn:
            conn.return_value.sock.version.return_value = "TLSv1.3"
            response = conn.return_value.getresponse.return_value
            response.status = 403
            response.getheader.return_value = "challenge"
            result = diagnose_host("chatgpt.com")
        self.assertEqual(result["outcome"], "challenge")
        self.assertEqual(result["http_status"], 403)
        self.assertEqual(result["tls"], "TLSv1.3")

    def test_remote_restart_refuses_process_drift_before_invoking_systemctl(self):
        payload = {"mode": "restart", "services": [{"id": "worker", "target": "worker.service", "kind": "systemd"}],
                   "expected": {"pid": 10}}
        with patch("app.remote_probe.inspect_services", return_value=[{"id": "worker", "pid": 20}]), \
             patch("app.remote_probe.command") as command:
            self.assertEqual(remote_run(payload)["error"], "service_changed")
            command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
