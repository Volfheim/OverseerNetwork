import json
import secrets
from hmac import compare_digest
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, Request, WebSocket
from app.paths import secret_path as default_secret_path


COOKIE_NAME = "overseer_access"


class AccessControl:
    def __init__(self, secret_path=None):
        self.secret_path = Path(secret_path) if secret_path else default_secret_path()
        self.access_token = self._load_or_create_token()

    def _load_or_create_token(self) -> str:
        if self.secret_path.exists():
            with self.secret_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            token = data.get("access_token")
            if token:
                return str(token)

        token = secrets.token_urlsafe(32)
        self.secret_path.parent.mkdir(parents=True, exist_ok=True)
        with self.secret_path.open("w", encoding="utf-8") as f:
            json.dump({"access_token": token}, f, indent=2)
        return token

    def validate(self, token: Optional[str]) -> bool:
        return bool(token) and compare_digest(str(token), self.access_token)

    def token_from_request(self, request: Request) -> Optional[str]:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            return auth_header.removeprefix("Bearer ").strip()
        return (
            request.query_params.get("access_token")
            or request.cookies.get(COOKIE_NAME)
        )

    def token_from_websocket(self, websocket: WebSocket) -> Optional[str]:
        return (
            websocket.query_params.get("access_token")
            or websocket.cookies.get(COOKIE_NAME)
        )

    def require_request(self, request: Request) -> None:
        if not self.validate(self.token_from_request(request)):
            raise HTTPException(status_code=401, detail="Overseer access key required")

    async def require_websocket(self, websocket: WebSocket) -> bool:
        if self.validate(self.token_from_websocket(websocket)):
            return True
        await websocket.close(code=1008)
        return False

    def access_url(self, host: str = "127.0.0.1", port: int = 2077) -> str:
        return f"http://{host}:{port}/?access_token={self.access_token}"


access_control = AccessControl()
