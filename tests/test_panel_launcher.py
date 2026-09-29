import contextlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from scripts import panel


class LauncherTests(unittest.TestCase):
    def test_open_uses_a_url_encoded_local_access_link(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "overseer.secrets.json").write_text(
                json.dumps({"access_token": "fixture token&value"}), encoding="utf-8"
            )
            with patch.object(panel, "secret_path", return_value=root / "overseer.secrets.json"), \
                 patch.object(panel, "lifecycle_lock", contextlib.nullcontext), \
                 patch.object(panel, "start") as start, \
                 patch.object(panel.webbrowser, "open") as browser, \
                 patch.object(sys, "argv", ["panel.py", "open"]):
                self.assertEqual(panel.main(), 0)
        start.assert_called_once_with()
        browser.assert_called_once_with("http://127.0.0.1:2077/?access_token=fixture+token%26value")

    def test_login_challenge_means_server_is_ready(self):
        from urllib.error import HTTPError
        with patch.object(panel, "build_opener") as factory:
            factory.return_value.open.side_effect = HTTPError(panel.URL, 401, "Login required", {}, None)
            self.assertTrue(panel.ready())

    @unittest.skipUnless(sys.platform == "win32", "Windows launcher flags")
    def test_background_server_breaks_away_from_the_launcher_console(self):
        self.assertTrue(panel.BACKGROUND_FLAGS & panel.subprocess.CREATE_BREAKAWAY_FROM_JOB)
        self.assertTrue(panel.BACKGROUND_FLAGS & panel.subprocess.DETACHED_PROCESS)

    def test_pid_reuse_is_not_owned(self):
        with patch.object(panel.psutil, "Process") as factory:
            factory.return_value.create_time.return_value = 200
            self.assertIsNone(panel.owned_process({"pid": 42, "created": 100, "instance": "test"}))

    def test_other_python_process_is_not_owned(self):
        with patch.object(panel.psutil, "Process") as factory:
            factory.return_value.create_time.return_value = 100
            factory.return_value.cmdline.return_value = ["python", "other.py", "serve", "test"]
            self.assertIsNone(panel.owned_process({"pid": 42, "created": 100, "instance": "test"}))

    def test_exact_launcher_identity_is_owned(self):
        with patch.object(panel.psutil, "Process") as factory:
            factory.return_value.create_time.return_value = 100
            factory.return_value.cmdline.return_value = ["pythonw", str(panel.SCRIPT), "serve", "--instance", "test"]
            self.assertIs(panel.owned_process({"pid": 42, "created": 100, "instance": "test"}), factory.return_value)

    def test_start_is_idempotent(self):
        with patch.object(panel, "owned_process", return_value=Mock()), patch.object(panel, "ready", return_value=True), patch.object(panel.subprocess, "Popen") as spawn:
            panel.start()
            spawn.assert_not_called()

    def test_unmanaged_listener_is_not_started_or_stopped(self):
        with patch.object(panel, "owned_process", return_value=None), patch.object(panel, "port_busy", return_value=True), patch.object(panel.subprocess, "Popen") as spawn:
            for action in [panel.start, panel.stop]:
                with self.assertRaisesRegex(RuntimeError, "unmanaged"):
                    action()
            spawn.assert_not_called()
