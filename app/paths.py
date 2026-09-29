"""Runtime data can live outside the application checkout."""
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def runtime_home():
    return Path(os.environ.get("OVERSEER_HOME", PROJECT_ROOT)).expanduser().resolve()


def config_path():
    return runtime_home() / "servers.yaml"


def secret_path():
    return runtime_home() / "overseer.secrets.json"


def data_dir():
    return runtime_home() / "data"
