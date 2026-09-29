import os
import re
from pathlib import Path
from typing import Dict, List, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from app.paths import PROJECT_ROOT, config_path as default_config_path


SERVER_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{2,48}$")


class ManagedService(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{2,48}$")
    label: str = Field(min_length=1, max_length=100)
    kind: Literal["systemd", "container"] = "systemd"
    target: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.@-]{0,127}$")
    required: bool = True
    allow_restart: bool = False

    @model_validator(mode="after")
    def canonical_unit(self):
        if self.kind == "systemd" and not self.target.endswith(".service"):
            self.target += ".service"
        return self


class ServerTemplate(BaseModel):
    """Reusable services and policy, copied into nodes rather than inherited live."""
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    name: str = Field(min_length=1, max_length=100)
    role: str = Field(default="node", max_length=100)
    tags: List[str] = Field(default_factory=list, max_length=32)
    environment: Literal["production", "lab", "personal"] = "personal"
    control_mode: Literal["observe", "operate"] = "observe"
    protected: bool = False
    protected_targets: List[str] = Field(default_factory=list, max_length=128)
    services: List[ManagedService] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def validate_services(self):
        if len({s.id for s in self.services}) != len(self.services):
            raise ValueError("Service IDs must be unique")
        if len({(s.kind, s.target) for s in self.services}) != len(self.services):
            raise ValueError("Service targets must be unique")
        for target in self.protected_targets:
            if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.@-]{0,127}", target):
                raise ValueError("Invalid protected service target")
        return self


class ServerConfig(ServerTemplate):
    type: Literal["local", "ssh"] = "ssh"
    host: Optional[str] = None
    port: int = Field(default=22, ge=1, le=65535)
    username: Optional[str] = None
    key_path: Optional[str] = None
    host_key: Optional[str] = Field(default=None, max_length=16384)
    coordinates: List[float] = Field(default_factory=lambda: [0.0, 0.0])
    # A local PC has a physical pin independent of its current VPN egress.
    physical_location: Optional[List[float]] = None
    city: Optional[str] = None
    country: Optional[str] = None
    provider: Optional[str] = None
    auto_geo: bool = True
    strict_host_key: bool = True
    notes: Optional[str] = None
    # Template provenance only. Effective services and policy belong to the node.
    profile: str = Field(default="generic", pattern=r"^[a-zA-Z0-9_-]{2,48}$")
    upstream_id: Optional[str] = None

    @model_validator(mode="after")
    def validate_connection(self):
        if self.type == "ssh" and (not self.host or not self.username):
            raise ValueError("SSH host and username are required")
        if self.upstream_id and not SERVER_ID_PATTERN.fullmatch(self.upstream_id):
            raise ValueError("Invalid upstream node ID")
        return self

    @field_validator("host_key")
    @classmethod
    def validate_host_key(cls, value):
        if not value:
            return None
        import asyncssh
        try:
            return asyncssh.import_public_key(value).export_public_key().decode().strip()
        except (ValueError, asyncssh.KeyImportError) as exc:
            raise ValueError("Invalid SSH public host key") from exc

    @field_validator("coordinates", "physical_location")
    @classmethod
    def validate_coordinates(cls, value: Optional[List[float]]) -> Optional[List[float]]:
        if value is None:
            return None
        if len(value) != 2:
            raise ValueError("coordinates must contain latitude and longitude")
        lat, lon = value
        if not -90 <= lat <= 90:
            raise ValueError("latitude must be between -90 and 90")
        if not -180 <= lon <= 180:
            raise ValueError("longitude must be between -180 and 180")
        return [float(lat), float(lon)]

    @field_validator("host")
    @classmethod
    def strip_host(cls, value: Optional[str]) -> Optional[str]:
        return value.strip() if value else value

    def resolved_key_path(self) -> Optional[str]:
        if not self.key_path:
            return None
        return str(Path(self.key_path).expanduser().absolute())

    def public_dict(self, server_id: str) -> dict:
        data = self.model_dump(exclude={"key_path"}, exclude_none=True)
        data["id"] = server_id
        data["has_key"] = bool(self.key_path)
        return data

    def storage_dict(self) -> dict:
        return self.model_dump(exclude_none=True)


class AppConfig:
    def __init__(self, config_path=None):
        self.config_path = Path(config_path) if config_path else default_config_path()
        self.servers: Dict[str, ServerConfig] = {}
        self.templates: Dict[str, ServerTemplate] = {}
        self.load_config()

    def reload_config(self) -> None:
        self.load_config()

    def load_config(self) -> None:
        if not self.config_path.exists():
            self.servers, self.templates = {}, {}
            return

        with self.config_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        if not isinstance(data, dict) or set(data) - {"schema_version", "servers", "templates"}:
            raise ValueError("Invalid registry document")
        if data.get("schema_version", 2) != 2:
            raise ValueError("Unsupported registry version")
        if not isinstance(data.get("servers", {}), dict):
            raise ValueError("Expected a server mapping")
        loaded: Dict[str, ServerConfig] = {}
        for server_id, server_data in data.get("servers", {}).items():
            self.validate_server_id(server_id)
            loaded[server_id] = ServerConfig(**(server_data or {}))

        templates = self.parse_templates(data.get("templates", {}))
        self.validate_topology(loaded)
        self.servers, self.templates = loaded, templates

    def save_config(self, servers=None, templates=None) -> None:
        servers = self.servers if servers is None else servers
        templates = self.templates if templates is None else templates
        data = {
            "schema_version": 2,
            "templates": {key: value.model_dump() for key, value in templates.items()},
            "servers": {
                server_id: server.storage_dict()
                for server_id, server in servers.items()
            }
        }
        tmp_path = self.config_path.with_suffix(self.config_path.suffix + ".tmp")
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with tmp_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
        os.replace(tmp_path, self.config_path)

    def get_server(self, server_id: str) -> ServerConfig:
        if server_id not in self.servers:
            raise ValueError(f"Server {server_id} not found in config.")
        return self.servers[server_id]

    def list_public_servers(self) -> List[dict]:
        return [
            server.public_dict(server_id)
            for server_id, server in self.servers.items()
        ]

    def upsert_server(self, server_id: str, payload: dict) -> ServerConfig:
        self.validate_server_id(server_id)
        server = ServerConfig(**payload)
        updated = {**self.servers, server_id: server}
        self.validate_topology(updated)
        self.save_config(updated)
        self.servers = updated
        return server

    def delete_server(self, server_id: str) -> None:
        self.validate_server_id(server_id)
        if server_id not in self.servers:
            raise KeyError(server_id)
        if any(s.upstream_id == server_id for s in self.servers.values()):
            raise ValueError("Node is referenced by another node's upstream")
        updated = {key: value for key, value in self.servers.items() if key != server_id}
        self.save_config(updated)
        self.servers = updated

    @staticmethod
    def validate_topology(servers):
        for server_id, server in servers.items():
            seen, parent = {server_id}, server.upstream_id
            while parent:
                if parent in seen or parent not in servers:
                    raise ValueError("Upstream must exist and must not form a cycle")
                seen.add(parent)
                parent = servers[parent].upstream_id

    @classmethod
    def parse_templates(cls, data):
        if not isinstance(data, dict) or len(data) > 100:
            raise ValueError("Expected at most 100 templates")
        result = {}
        for key, value in data.items():
            cls.validate_server_id(key)
            result[key] = ServerTemplate.model_validate(value)
        return result

    def upsert_template(self, template_id, payload):
        templates = self.parse_templates({**{k: v.model_dump() for k, v in self.templates.items()}, template_id: payload})
        self.save_config(templates=templates)
        self.templates = templates
        return templates[template_id]

    def delete_template(self, template_id):
        if template_id not in self.templates:
            raise KeyError(template_id)
        templates = {k: v for k, v in self.templates.items() if k != template_id}
        self.save_config(templates=templates)
        self.templates = templates

    def export_registry(self):
        return {"schema_version": 2,
                "servers": {k: v.model_dump(exclude={"key_path"}, exclude_none=True) for k, v in self.servers.items()},
                "templates": {k: v.model_dump() for k, v in self.templates.items()}}

    def import_registry(self, data, preview=False):
        if not isinstance(data, dict) or set(data) != {"schema_version", "servers", "templates"} or data["schema_version"] != 2:
            raise ValueError("Expected an Overseer v2 registry export")
        if not isinstance(data["servers"], dict) or len(data["servers"]) > 200:
            raise ValueError("Expected at most 200 servers")
        templates = self.parse_templates(data["templates"])
        if self.servers.keys() & data["servers"].keys() or self.templates.keys() & templates.keys():
            raise ValueError("Import conflicts with existing IDs; nothing was changed")
        servers = {}
        for key, value in data["servers"].items():
            self.validate_server_id(key)
            if not isinstance(value, dict) or "key_path" in value:
                raise ValueError("SSH credentials must be selected locally, not imported")
            node = ServerConfig.model_validate(value)
            servers[key] = node.model_copy(update={"control_mode": "observe", "strict_host_key": True, "host_key": None})
        merged = {**self.servers, **servers}
        self.validate_topology(merged)
        if not preview:
            updated_templates = {**self.templates, **templates}
            self.save_config(merged, updated_templates)
            self.servers, self.templates = merged, updated_templates
        return {"servers": len(servers), "templates": len(templates), "mode": "observe"}

    @staticmethod
    def validate_server_id(server_id: str) -> None:
        if not isinstance(server_id, str) or not SERVER_ID_PATTERN.fullmatch(server_id):
            raise ValueError("server id must be 2-48 chars: letters, numbers, _ or -")


config = AppConfig()
