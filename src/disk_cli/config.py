from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

DEFAULT_APP_ID = "R41nXZEwpfl6tw6KYyoEZ4FYST4BXPlF"
DEFAULT_BACKEND_URL = "https://disk.6-79.cn"
DEFAULT_QINIU_UPLOAD_URL = "https://up.qiniup.com"
DEFAULT_SSO_URL = "https://sso.6-79.cn/oauth/"
DEFAULT_STATE_PATH = Path.home() / ".config" / "disk-cli" / "state.json"


@dataclass
class DiskSettings:
    backend_url: str = field(default_factory=lambda: os.getenv("DISK_BACKEND_URL", DEFAULT_BACKEND_URL))
    sso_url: str = field(default_factory=lambda: os.getenv("DISK_SSO_URL", DEFAULT_SSO_URL))
    sso_app_id: str = field(default_factory=lambda: os.getenv("DISK_SSO_APP_ID", DEFAULT_APP_ID))
    qiniu_upload_url: str = field(default_factory=lambda: os.getenv("DISK_QINIU_UPLOAD_URL", DEFAULT_QINIU_UPLOAD_URL))
    token: str | None = None
    user: dict[str, Any] | None = None
    state_path: Path = DEFAULT_STATE_PATH

    @classmethod
    def load(cls, state_path: Path | None = None) -> "DiskSettings":
        resolved_path = (state_path or DEFAULT_STATE_PATH).expanduser()
        if not resolved_path.exists():
            return cls(state_path=resolved_path)

        data = json.loads(resolved_path.read_text(encoding="utf-8"))
        return cls(
            backend_url=data.get("backend_url", os.getenv("DISK_BACKEND_URL", DEFAULT_BACKEND_URL)),
            sso_url=data.get("sso_url", os.getenv("DISK_SSO_URL", DEFAULT_SSO_URL)),
            sso_app_id=data.get("sso_app_id", os.getenv("DISK_SSO_APP_ID", DEFAULT_APP_ID)),
            qiniu_upload_url=data.get(
                "qiniu_upload_url",
                os.getenv("DISK_QINIU_UPLOAD_URL", DEFAULT_QINIU_UPLOAD_URL),
            ),
            token=data.get("token"),
            user=data.get("user"),
            state_path=resolved_path,
        )

    def save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(self)
        payload["state_path"] = str(self.state_path)
        self.state_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def clear_auth(self) -> None:
        self.token = None
        self.user = None

    def oauth_authorize_url(self, *, state: str | None = "disk-cli") -> str:
        params = {"app_id": self.sso_app_id}
        if state:
            params["state"] = state
        return f"{self.sso_url}?{urlencode(params)}"
