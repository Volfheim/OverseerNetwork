import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from scripts.source_bundle import audit, build_archive, source_files


class SourceBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ["app/main.py", "static/js/app.js", "static/img/three-globe/LICENSE", "README.md",
                     "servers.yaml", "data/private.py", "venv/secret.py", "app/__pycache__/main.pyc",
                     "artifacts/shot.png", "static/audio/legacy.wav", "static/img/earth-old.png"]:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# fixture", encoding="utf-8")
        (self.root / "servers.yaml").write_text("servers: {}", encoding="utf-8")
        (self.root / "overseer.secrets.json").write_text(json.dumps({"access_token": "private-fixture-token"}), encoding="utf-8")

    def test_archive_contains_source_but_no_local_data_or_legacy_media(self):
        archive = self.root / "dist" / "test.zip"
        build_archive(archive, self.root, self.root)
        with zipfile.ZipFile(archive) as source:
            self.assertEqual(set(source.namelist()), {"app/main.py", "static/js/app.js", "static/img/three-globe/LICENSE", "README.md"})

    def test_secret_value_inside_source_blocks_export_without_echoing_secret(self):
        (self.root / "app/main.py").write_text("TOKEN = 'private-fixture-token'", encoding="utf-8")
        with self.assertRaises(ValueError) as result:
            audit(source_files(self.root), self.root, self.root)
        self.assertIn("app", str(result.exception))
        self.assertNotIn("private-fixture-token", str(result.exception))

    def test_private_host_and_key_path_are_detected(self):
        (self.root / "servers.yaml").write_text("servers: {ss: {host: private.example.invalid, key_path: /home/private/key}}", encoding="utf-8")
        for value in ("private.example.invalid", "/home/private/key"):
            (self.root / "README.md").write_text(value, encoding="utf-8")
            with self.assertRaises(ValueError):
                audit(source_files(self.root), self.root, self.root)

    def test_private_key_marker_blocks_export(self):
        marker = "-----BEGIN " + "OPENSSH PRIVATE KEY-----"
        (self.root / "app/main.py").write_text(marker, encoding="utf-8")
        with self.assertRaises(ValueError):
            audit(source_files(self.root), self.root, self.root)


if __name__ == "__main__":
    unittest.main()
