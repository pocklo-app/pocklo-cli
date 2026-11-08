from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from shutil import which
from tempfile import NamedTemporaryFile
from typing import Any, Dict, Optional
from urllib.parse import urlencode

DEFAULT_APP_ID = "R41nXZEw"
DEFAULT_BACKEND_URL = "https://disk.6-79.cn"
DEFAULT_SSO_URL = "https://qt.6-79.cn/oauth/"
DEFAULT_STATE_PATH = Path.home() / ".config" / "pocklo-cli" / "state.json"
DEFAULT_UPLOAD_RECORD_DIR = DEFAULT_STATE_PATH.with_name("upload-records")
LEGACY_STATE_PATHS = tuple(
    Path.home() / ".config" / name / "state.json" for name in ("htx-cli", "disk-cli")
)
LEGACY_APP_IDS = {"R41nXZEwpfl6tw6KYyoEZ4FYST4BXPlF"}
LEGACY_SSO_URLS = {
    "https://sso.6-79.cn/oauth",
    "https://sso.6-79.cn/oauth/",
}
DEFAULT_QT_COMMAND = "qt"


def env_setting(name: str, default: str) -> str:
    return os.getenv(f"POCKLO_{name}", os.getenv(f"DISK_{name}", default))


@dataclass
class PockloSettings:
    backend_url: str = field(default_factory=lambda: env_setting("BACKEND_URL", DEFAULT_BACKEND_URL))
    sso_url: str = field(default_factory=lambda: env_setting("SSO_URL", DEFAULT_SSO_URL))
    sso_app_id: str = field(default_factory=lambda: env_setting("SSO_APP_ID", DEFAULT_APP_ID))
    qt_command: str = field(default_factory=lambda: env_setting("QT_COMMAND", DEFAULT_QT_COMMAND))
    token: Optional[str] = None
    user: Optional[Dict[str, Any]] = None
    cwd_res_id: Optional[str] = None
    cwd_path: str = "/"
    state_path: Path = DEFAULT_STATE_PATH
    upload_record_dir: Path = field(
        default_factory=lambda: Path(
            env_setting("UPLOAD_RECORD_DIR", str(DEFAULT_UPLOAD_RECORD_DIR))
        ).expanduser()
    )

    @staticmethod
    def _default_sso_url() -> str:
        return env_setting("SSO_URL", DEFAULT_SSO_URL)

    @staticmethod
    def _default_sso_app_id() -> str:
        return env_setting("SSO_APP_ID", DEFAULT_APP_ID)

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
    def load(cls, state_path: Optional[Path] = None) -> "PockloSettings":
        preferred_path = (state_path or DEFAULT_STATE_PATH).expanduser()
        source_path = preferred_path
        loaded_from_legacy_path = False
        if state_path is None and not preferred_path.exists():
            source_path = next((path for path in LEGACY_STATE_PATHS if path.exists()), preferred_path)
            loaded_from_legacy_path = source_path != preferred_path
        if not source_path.exists():
            return cls(state_path=preferred_path)

        data = json.loads(source_path.read_text(encoding="utf-8"))
        normalized_sso_url, normalized_sso_app_id = cls._normalize_oauth_settings(
            sso_url=data.get("sso_url"),
            sso_app_id=data.get("sso_app_id"),
        )
        default_records = source_path.with_name("upload-records") if loaded_from_legacy_path else DEFAULT_UPLOAD_RECORD_DIR
        settings = cls(
            backend_url=data.get("backend_url", env_setting("BACKEND_URL", DEFAULT_BACKEND_URL)),
            sso_url=normalized_sso_url,
            sso_app_id=normalized_sso_app_id,
            qt_command=data.get("qt_command", env_setting("QT_COMMAND", DEFAULT_QT_COMMAND)),
            token=data.get("token"),
            user=data.get("user"),
            cwd_res_id=data.get("cwd_res_id"),
            cwd_path=data.get("cwd_path") or "/",
            state_path=preferred_path if loaded_from_legacy_path else source_path,
            upload_record_dir=Path(
                data.get(
                    "upload_record_dir",
                    env_setting("UPLOAD_RECORD_DIR", str(default_records)),
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
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.state_path.parent, delete=False) as stream:
            temp_path = Path(stream.name)
            try:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.close()
                temp_path.replace(self.state_path)
            finally:
                temp_path.unlink(missing_ok=True)

    def clear_auth(self) -> None:
        self.token = None
        self.user = None
        self.cwd_res_id = None
        self.cwd_path = "/"

    def oauth_authorize_url(self, *, state: Optional[str] = "pocklo-cli") -> str:
        params = {"app_id": self.sso_app_id}
        if state:
            params["state"] = state
        return f"{self.sso_url}?{urlencode(params)}"

    def resolve_qt_command(self) -> Optional[str]:
        return which(self.qt_command) or (self.qt_command if Path(self.qt_command).exists() else None)
