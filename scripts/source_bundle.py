"""Build a reviewable source-only archive, never a copy of the working directory."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import zipfile

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.paths import runtime_home

ROOT_FILES = {
    ".gitignore", "LICENSE", "README.md", "README.en.md", "THIRD_PARTY_NOTICES.md",
    "requirements.txt", "requirements-dev.txt", "servers.example.yaml", "overseer.cmd",
    "Overseer_Launcher.bat", "\u0417\u0430\u043f\u0443\u0441\u043a_\u0421\u043c\u043e\u0442\u0440\u0438\u0442\u0435\u043b\u044f.bat",
}
PATTERNS = (
    "app/*.py", "scripts/*.py", "scripts/*.ps1", "scripts/*.cs",
    "tests/*.py", "tests/*.test.cjs", "docs/*.md", "templates/*.html",
    "static/js/*.js", "static/css/*.css", "static/icons/*.svg", "static/icons/LICENSE",
    "static/manifest.webmanifest", "static/img/overseer-icon.svg",
    "static/img/three-globe/LICENSE", "static/img/three-globe/earth-blue-marble.jpg",
    "static/img/three-globe/earth-topology.png", "static/img/three-globe/earth-water.png",
)
KEY_SIGNATURE = re.compile(rb"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----")


def source_files(root=ROOT):
    candidates = {root / name for name in ROOT_FILES if (root / name).is_file()}
    for pattern in PATTERNS:
        candidates.update(root.glob(pattern))
    result = []
    for path in sorted(candidates):
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("Source includes a non-regular or external path: " + str(path.relative_to(root)))
        result.append(path)
    return result


def private_values(home):
    values = set()
    secret = home / "overseer.secrets.json"
    if secret.exists():
        token = json.loads(secret.read_text(encoding="utf-8")).get("access_token")
        if token:
            values.add(str(token))
    registry = home / "servers.yaml"
    if registry.exists():
        data = yaml.safe_load(registry.read_text(encoding="utf-8")) or {}
        for node in data.get("servers", {}).values():
            for field in ("host", "key_path"):
                value = node.get(field)
                if value and value not in {"localhost", "127.0.0.1", "::1"}:
                    values.add(str(value))
    return values


def audit(files, root=ROOT, home=None):
    needles = {s.encode() for s in private_values(home or runtime_home()) if len(s) >= 6}
    failures = []
    for path in files:
        data = path.read_bytes()
        if KEY_SIGNATURE.search(data) or any(value in data for value in needles):
            failures.append(str(path.relative_to(root)))
    if failures:
        raise ValueError("Possible private data in source files: " + ", ".join(failures))


def build_archive(target, root=ROOT, home=None):
    files = source_files(root)
    audit(files, root, home)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(root).as_posix())
    temporary.replace(target)
    return len(files)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Audit allowlisted files without creating an archive")
    args = parser.parse_args()
    try:
        files = source_files()
        audit(files)
        if args.check:
            print(f"Source audit passed: {len(files)} allowlisted files; private runtime data excluded.")
        else:
            target = ROOT / "dist" / "overseer-source.zip"
            count = build_archive(target)
            print(f"Created {target} ({count} files)")
            print("SHA256 " + hashlib.sha256(target.read_bytes()).hexdigest())
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
