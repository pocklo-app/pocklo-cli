from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from shutil import which
from typing import Any, Dict, Optional
from urllib.parse import urlencode

DEFAULT_APP_ID = "R41nXZEw"
DEFAULT_BACKEND_URL = "https://disk.6-79.cn"
DEFAULT_SSO_URL = "https://qt.6-79.cn/oauth/"
DEFAULT_STATE_PATH = Path.home() / ".config" / "htx-cli" / "state.json"
DEFAULT_UPLOAD_RECORD_DIR = Path.home() / ".config" / "htx-cli" / "upload-records"
LEGACY_STATE_PATH = Path.home() / ".config" / "disk-cli" / "state.json"
DEFAULT_QT_COMMAND = "qt"


@dataclass
class DiskSettings:
    backend_url: str = field(default_factory=lambda: os.getenv("DISK_BACKEND_URL", DEFAULT_BACKEND_URL))
    sso_url: str = field(default_factory=lambda: os.getenv("DISK_SSO_URL", DEFAULT_SSO_URL))
    sso_app_id: str = field(default_factory=lambda: os.getenv("DISK_SSO_APP_ID", DEFAULT_APP_ID))
    qt_command: str = field(default_factory=lambda: os.getenv("DISK_QT_COMMAND", DEFAULT_QT_COMMAND))
    token: Optional[str] = None
    user: Optional[Dict[str, Any]] = None
    state_path: Path = DEFAULT_STATE_PATH
    upload_record_dir: Path = field(
        default_factory=lambda: Path(
            os.getenv("DISK_UPLOAD_RECORD_DIR", str(DEFAULT_UPLOAD_RECORD_DIR))
        ).expanduser()
    )

    @classmethod
    def load(cls, state_path: Optional[Path] = None) -> "DiskSettings":
        resolved_path = (state_path or DEFAULT_STATE_PATH).expanduser()
        if state_path is None and not resolved_path.exists() and LEGACY_STATE_PATH.exists():
            resolved_path = LEGACY_STATE_PATH
        if not resolved_path.exists():
            return cls(state_path=resolved_path)

        data = json.loads(resolved_path.read_text(encoding="utf-8"))
        return cls(
            backend_url=data.get("backend_url", os.getenv("DISK_BACKEND_URL", DEFAULT_BACKEND_URL)),
            sso_url=data.get("sso_url", os.getenv("DISK_SSO_URL", DEFAULT_SSO_URL)),
            sso_app_id=data.get("sso_app_id", os.getenv("DISK_SSO_APP_ID", DEFAULT_APP_ID)),
            qt_command=data.get("qt_command", os.getenv("DISK_QT_COMMAND", DEFAULT_QT_COMMAND)),
            token=data.get("token"),
            user=data.get("user"),
            state_path=resolved_path,
            upload_record_dir=Path(
                data.get(
                    "upload_record_dir",
                    os.getenv("DISK_UPLOAD_RECORD_DIR", str(DEFAULT_UPLOAD_RECORD_DIR)),
                )
            ).expanduser(),
        )

    def save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(self)
        payload["state_path"] = str(self.state_path)
        payload["upload_record_dir"] = str(self.upload_record_dir)
        self.state_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def clear_auth(self) -> None:
        self.token = None
        self.user = None

    def oauth_authorize_url(self, *, state: Optional[str] = "htx-cli") -> str:
        params = {"app_id": self.sso_app_id}
        if state:
            params["state"] = state
        return f"{self.sso_url}?{urlencode(params)}"

    def resolve_qt_command(self) -> Optional[str]:
        return which(self.qt_command) or (self.qt_command if Path(self.qt_command).exists() else None)
