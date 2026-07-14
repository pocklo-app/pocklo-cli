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
LEGACY_APP_IDS = {"R41nXZEwpfl6tw6KYyoEZ4FYST4BXPlF"}
LEGACY_SSO_URLS = {
    "https://sso.6-79.cn/oauth",
    "https://sso.6-79.cn/oauth/",
}
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

    @staticmethod
    def _default_sso_url() -> str:
        return os.getenv("DISK_SSO_URL", DEFAULT_SSO_URL)

    @staticmethod
    def _default_sso_app_id() -> str:
        return os.getenv("DISK_SSO_APP_ID", DEFAULT_APP_ID)

    @classmethod
    def _normalize_oauth_settings(
        cls,
        *,
        sso_url: Optional[str],
        sso_app_id: Optional[str],
    ) -> tuple[str, str]:
        normalized_sso_url = sso_url or cls._default_sso_url()
        normalized_sso_app_id = sso_app_id or cls._default_sso_app_id()

        if normalized_sso_url in LEGACY_SSO_URLS:
            normalized_sso_url = cls._default_sso_url()
        if normalized_sso_app_id in LEGACY_APP_IDS:
            normalized_sso_app_id = cls._default_sso_app_id()

        return normalized_sso_url, normalized_sso_app_id

    @classmethod
    def load(cls, state_path: Optional[Path] = None) -> "DiskSettings":
        preferred_path = (state_path or DEFAULT_STATE_PATH).expanduser()
        source_path = preferred_path
        loaded_from_legacy_path = False
        if state_path is None and not preferred_path.exists() and LEGACY_STATE_PATH.exists():
            source_path = LEGACY_STATE_PATH
            loaded_from_legacy_path = True
        if not source_path.exists():
            return cls(state_path=preferred_path)

        data = json.loads(source_path.read_text(encoding="utf-8"))
        normalized_sso_url, normalized_sso_app_id = cls._normalize_oauth_settings(
            sso_url=data.get("sso_url"),
            sso_app_id=data.get("sso_app_id"),
        )
        settings = cls(
            backend_url=data.get("backend_url", os.getenv("DISK_BACKEND_URL", DEFAULT_BACKEND_URL)),
            sso_url=normalized_sso_url,
            sso_app_id=normalized_sso_app_id,
            qt_command=data.get("qt_command", os.getenv("DISK_QT_COMMAND", DEFAULT_QT_COMMAND)),
            token=data.get("token"),
            user=data.get("user"),
            state_path=preferred_path if loaded_from_legacy_path else source_path,
            upload_record_dir=Path(
                data.get(
                    "upload_record_dir",
                    os.getenv("DISK_UPLOAD_RECORD_DIR", str(DEFAULT_UPLOAD_RECORD_DIR)),
                )
            ).expanduser(),
        )
        should_resave = loaded_from_legacy_path or (
            data.get("sso_url") != normalized_sso_url
            or data.get("sso_app_id") != normalized_sso_app_id
        )
        if should_resave:
            settings.save()
        return settings

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
