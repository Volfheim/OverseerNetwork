import json
import os
import socket
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch
from urllib.error import HTTPError
from urllib.request import ProxyHandler, build_opener

import asyncssh
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import AppConfig, ServerConfig
from app.paths import runtime_home, secret_path
from app.security import AccessControl, access_control
from app.setup_api import build_setup_router
from app.ssh_transport import ProbeError, connect, connection_options


def node(**updates):
    return {"name": "Example", "host": "example.invalid", "username": "operator", **updates}


class RegistryFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.registry = AppConfig(self.root / "private" / "servers.yaml")


class RegistrySetupTests(RegistryFixture, unittest.TestCase):
    def test_first_run_empty_then_persists_first_node_without_sample_file(self):
        self.assertEqual(self.registry.servers, {})
        self.registry.upsert_server("pc", {"name": "My PC", "type": "local"})
        self.assertEqual(AppConfig(self.registry.config_path).get_server("pc").type, "local")
        self.registry.delete_server("pc")
        self.assertEqual(AppConfig(self.registry.config_path).servers, {})

    def test_templates_are_data_and_node_services_do_not_inherit_updates(self):
        template = {"name": "Web", "protected": True, "services": [
            {"id": "web", "label": "Web", "target": "example-web"}]}
        self.registry.upsert_template("custom-web", template)
        self.registry.upsert_server("web", node(**template, profile="custom-web"))
        self.registry.upsert_template("custom-web", {"name": "Changed", "services": []})
        self.registry.delete_template("custom-web")
        saved = AppConfig(self.registry.config_path).get_server("web")
        self.assertEqual(saved.services[0].target, "example-web.service")
        self.assertTrue(saved.protected)

    def test_template_write_failure_is_atomic(self):
        self.registry.upsert_template("before", {"name": "Before"})
        before = self.registry.config_path.read_bytes()
        with patch("app.config.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.registry.upsert_template("after", {"name": "After"})
        self.assertEqual(set(self.registry.templates), {"before"})
        self.assertEqual(self.registry.config_path.read_bytes(), before)

    def export_fixture(self):
        key = asyncssh.generate_private_key("ssh-ed25519").export_public_key().decode()
        self.registry.upsert_template("web", {"name": "Web"})
        self.registry.upsert_server("parent", node(key_path="private-local-key", control_mode="operate", host_key=key))
        self.registry.upsert_server("child", node(upstream_id="parent"))
        return self.registry.export_registry()

    def test_export_import_preserves_topology_without_credentials_or_trust(self):
        data = self.export_fixture()
        self.assertNotIn("private-local-key", json.dumps(data))
        self.assertNotIn("key_path", json.dumps(data))
        target = AppConfig(self.root / "target.yaml")
        self.assertEqual(target.import_registry(data, preview=True)["servers"], 2)
        self.assertFalse(target.config_path.exists())
        target.import_registry(data)
        reloaded = AppConfig(target.config_path)
        self.assertEqual(reloaded.get_server("child").upstream_id, "parent")
        self.assertEqual(reloaded.get_server("parent").control_mode, "observe")
        self.assertIsNone(reloaded.get_server("parent").host_key)
        self.assertTrue(reloaded.get_server("parent").strict_host_key)

    def test_conflicting_or_invalid_import_does_not_partially_write(self):
        data = self.export_fixture()
        before = self.registry.config_path.read_bytes()
        with self.assertRaises(ValueError):
            self.registry.import_registry(data)
        self.assertEqual(self.registry.config_path.read_bytes(), before)
        target = AppConfig(self.root / "target.yaml")
        data["servers"]["child"]["upstream_id"] = "missing"
        with self.assertRaises(ValueError):
            target.import_registry(data)
        self.assertEqual(target.servers, {})
        self.assertFalse(target.config_path.exists())

    def test_import_rejects_local_credential_paths(self):
        data = {"schema_version": 2, "templates": {}, "servers": {"ss": node(key_path="/private/key")}}
        with self.assertRaises(ValueError):
            self.registry.import_registry(data)

    def test_reload_rejects_cycles_and_preserves_live_registry(self):
        self.registry.upsert_server("pc", {"name": "PC", "type": "local"})
        self.registry.config_path.write_text('servers: {ss: {name: Bad, type: local, upstream_id: ss}}', encoding="utf-8")
        with self.assertRaises(ValueError):
            self.registry.reload_config()
        self.assertEqual(set(self.registry.servers), {"pc"})

    def test_isolated_home_owns_config_secret_db_and_logs(self):
        home = self.root / "clean"
        env = {**os.environ, "OVERSEER_HOME": str(home)}
        result = subprocess.run([sys.executable, "-c",
            "from app.main import config; from app.fleet_api import operations; assert config.servers == {}; print('clean-start-ok')"],
            env=env, capture_output=True, text=True, timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("clean-start-ok", result.stdout)
        self.assertTrue((home / "overseer.secrets.json").exists())
        self.assertTrue((home / "data" / "operations.sqlite3").exists())
        with patch.dict(os.environ, {"OVERSEER_HOME": str(home)}):
            self.assertEqual(secret_path().parent, runtime_home())
            self.assertEqual(AccessControl().secret_path, secret_path())

    def test_clean_authenticated_page_renders_without_existing_inventory(self):
        env = {**os.environ, "OVERSEER_HOME": str(self.root / "web")}
        script = (
            "from fastapi.testclient import TestClient; from app.main import app; "
            "from app.security import access_control, COOKIE_NAME; "
            "client=TestClient(app); client.cookies.set(COOKIE_NAME,access_control.access_token); "
            "response=client.get('/'); assert response.status_code==200; "
            "assert 'first-run' in response.text; "
            "assert client.get('/api/setup/templates').json() == {'templates': {}}"
        )
        result = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=20,
                                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_portable_launcher_serves_a_clean_home_on_requested_port(self):
        with socket.socket() as available:
            available.bind(("127.0.0.1", 0))
            port = available.getsockname()[1]
        home = self.root / "launched"
        process = subprocess.Popen([sys.executable, "-m", "app", "--port", str(port)],
            env={**os.environ, "OVERSEER_HOME": str(home)}, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        try:
            deadline = time.monotonic() + 15
            reached = False
            while time.monotonic() < deadline and process.poll() is None:
                try:
                    build_opener(ProxyHandler({})).open(f"http://127.0.0.1:{port}/", timeout=0.5).close()
                except HTTPError as error:
                    if error.code == 401:
                        reached = True
                        break
                except OSError:
                    time.sleep(0.1)
            self.assertTrue(reached, "Fresh launcher did not serve the login page")
            self.assertTrue((home / "overseer.secrets.json").exists())
        finally:
            process.terminate()
            process.wait(timeout=10)


class SetupApiTests(RegistryFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        app = FastAPI()
        app.include_router(build_setup_router(self.registry, lambda: None))
        self.client = TestClient(app)
        self.headers = {"Authorization": "Bearer " + access_control.access_token}

    def test_all_setup_endpoints_require_auth(self):
        for method, route in [("GET", "/templates"), ("PUT", "/templates/test"), ("DELETE", "/templates/test"),
                              ("GET", "/export"), ("POST", "/import"), ("POST", "/host-key"), ("POST", "/connection")]:
            with self.subTest(route=route):
                self.assertEqual(self.client.request(method, "/api/setup" + route).status_code, 401)

    def test_template_crud(self):
        response = self.client.put("/api/setup/templates/web", headers=self.headers, json={"name": "Web"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("web", self.client.get("/api/setup/templates", headers=self.headers).json()["templates"])
        self.assertEqual(self.client.delete("/api/setup/templates/web", headers=self.headers).status_code, 200)
        self.assertEqual(self.client.delete("/api/setup/templates/web", headers=self.headers).status_code, 404)

    def test_import_preview_is_read_only_and_large_body_is_rejected(self):
        data = {"schema_version": 2, "servers": {"pc": {"name": "PC", "type": "local"}}, "templates": {}}
        self.assertEqual(self.client.post("/api/setup/import", headers=self.headers, json=data).status_code, 200)
        self.assertEqual(self.registry.servers, {})
        self.assertEqual(self.client.post("/api/setup/import?preview=false", headers=self.headers, json=data).status_code, 200)
        self.assertIn("pc", self.registry.servers)
        self.assertEqual(self.client.post("/api/setup/import", headers=self.headers, content=b'x' * 1048577).status_code, 413)

    def test_scanning_key_does_not_trust_or_store_it(self):
        key = asyncssh.generate_private_key("ssh-ed25519")
        with patch("app.setup_api.asyncssh.get_server_host_key", new=AsyncMock(return_value=key)):
            response = self.client.post("/api/setup/host-key", headers=self.headers, json={"host": "example.invalid"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["verified"])
        self.assertEqual(response.json()["fingerprint"], key.get_fingerprint())
        self.assertEqual(self.registry.servers, {})

    def test_pinned_host_key_is_enforced_by_transport(self):
        key = asyncssh.generate_private_key("ssh-ed25519")
        saved = self.registry.upsert_server("ss", node(host_key=key.export_public_key().decode()))
        opts = connection_options(saved)
        self.assertEqual(opts["known_hosts"][0][0].get_fingerprint(), key.get_fingerprint())
        self.assertNotEqual(opts["known_hosts"], None)

    def test_connection_errors_do_not_echo_private_key_content(self):
        with patch("app.setup_api.connect", side_effect=ValueError("PRIVATE CONTENT")):
            response = self.client.post("/api/setup/connection", headers=self.headers,
                                        json={"host": "example.invalid", "username": "operator"})
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("PRIVATE CONTENT", response.text)


class HostKeyHandshakeTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_ssh_accepts_exact_pinned_key_and_rejects_a_different_one(self):
        class FixtureServer(asyncssh.SSHServer):
            def begin_auth(self, username):
                return False

        key = asyncssh.generate_private_key("ssh-ed25519")
        listener = await asyncssh.listen("127.0.0.1", 0, server_factory=FixtureServer, server_host_keys=[key])
        try:
            server = ServerConfig(name="Fixture", host="127.0.0.1", port=listener.get_port(), username="fixture",
                                  host_key=key.export_public_key().decode())
            async with connect(server) as connection:
                self.assertFalse(connection.is_closed())
            wrong_key = asyncssh.generate_private_key("ssh-ed25519").export_public_key().decode()
            with self.assertRaisesRegex(ProbeError, "host_key_unverified"):
                async with connect(server.model_copy(update={"host_key": wrong_key})):
                    self.fail("A different key must not be trusted")
        finally:
            listener.close()
            await listener.wait_closed()


if __name__ == "__main__":
    unittest.main()
