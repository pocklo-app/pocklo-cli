from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests

from .config import DiskSettings


class DiskAPIError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        identifier: str | None = None,
        debug_msg: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.identifier = identifier
        self.debug_msg = debug_msg


@dataclass
class UploadToken:
    key: str
    upload_token: str


class DiskClient:
    def __init__(self, settings: DiskSettings, *, timeout: float = 30.0) -> None:
        self.settings = settings
        self.timeout = timeout
        self.session = requests.Session()

    def _headers(self, *, include_auth: bool = True) -> dict[str, str]:
        headers: dict[str, str] = {}
        if include_auth and self.settings.token:
            headers["Token"] = self.settings.token
        return headers

    def _unwrap_response(self, response: requests.Response) -> Any:
        try:
            payload = response.json()
        except ValueError as exc:
            raise DiskAPIError(
                f"Server returned a non-JSON response (HTTP {response.status_code})."
            ) from exc

        if not isinstance(payload, dict) or "code" not in payload:
            return payload

        if payload.get("code") != 0:
            raise DiskAPIError(
                payload.get("msg") or "The Disk API request failed.",
                status_code=response.status_code,
                identifier=payload.get("identifier"),
                debug_msg=payload.get("debug_msg"),
            )

        return payload.get("body")

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        absolute_url: str | None = None,
        include_auth: bool = True,
        data: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
    ) -> Any:
        url = absolute_url or urljoin(f"{self.settings.backend_url}/", path.lstrip("/"))
        response = self.session.request(
            method=method.upper(),
            url=url,
            params=params,
            json=json,
            data=data,
            files=files,
            headers=self._headers(include_auth=include_auth),
            timeout=self.timeout,
        )
        return self._unwrap_response(response)

    def exchange_code(self, code: str) -> dict[str, Any]:
        body = self.request(
            "GET",
            "/api/oauth/qtb/callback",
            params={"code": code},
            include_auth=False,
        )
        if not isinstance(body, dict) or "token" not in body:
            raise DiskAPIError("OAuth callback did not return a login token.")
        self.settings.token = body["token"]
        return body

    def get_current_user(self) -> dict[str, Any]:
        body = self.request("GET", "/api/user/")
        if not isinstance(body, dict):
            raise DiskAPIError("User endpoint returned an unexpected payload.")
        return body

    def get_resource(self, res_str_id: str, visit_key: str | None = None) -> dict[str, Any]:
        params = {"visit_key": visit_key} if visit_key else None
        body = self.request("GET", f"/api/res/{res_str_id}", params=params)
        if not isinstance(body, dict):
            raise DiskAPIError("Resource endpoint returned an unexpected payload.")
        return body

    def get_selector(self, res_str_id: str) -> dict[str, Any]:
        body = self.request("GET", f"/api/res/{res_str_id}/selector")
        if not isinstance(body, dict):
            raise DiskAPIError("Selector endpoint returned an unexpected payload.")
        return body

    def create_folder(self, parent_id: str, folder_name: str) -> dict[str, Any]:
        body = self.request(
            "POST",
            f"/api/res/{parent_id}/folder",
            json={"folder_name": folder_name},
        )
        if not isinstance(body, dict):
            raise DiskAPIError("Create-folder endpoint returned an unexpected payload.")
        return body

    def create_link(self, parent_id: str, link_name: str, link: str) -> dict[str, Any]:
        body = self.request(
            "POST",
            f"/api/res/{parent_id}/link",
            json={"link_name": link_name, "link": link},
        )
        if not isinstance(body, dict):
            raise DiskAPIError("Create-link endpoint returned an unexpected payload.")
        return body

    def update_resource(
        self,
        res_str_id: str,
        *,
        rname: str | None = None,
        status: int | None = None,
        description: str | None = None,
        visit_key: str | None = None,
        right_bubble: bool | None = None,
        parent_str_id: str | None = None,
    ) -> dict[str, Any]:
        body = self.request(
            "PUT",
            f"/api/res/{res_str_id}",
            json={
                "rname": rname,
                "status": status,
                "description": description,
                "visit_key": visit_key,
                "right_bubble": right_bubble,
                "parent_str_id": parent_str_id,
            },
        )
        if not isinstance(body, dict):
            raise DiskAPIError("Update-resource endpoint returned an unexpected payload.")
        return body

    def delete_resource(self, res_str_id: str) -> None:
        self.request("DELETE", f"/api/res/{res_str_id}")

    def download_resource(self, res_str_id: str, destination: Path) -> str:
        url = urljoin(f"{self.settings.backend_url}/", f"/api/res/{res_str_id}/dl".lstrip("/"))
        response = self.session.get(
            url,
            params={"token": self.settings.token or ""},
            headers={},
            timeout=self.timeout,
            stream=True,
            allow_redirects=True,
        )
        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            self._unwrap_response(response)
            raise DiskAPIError("Download endpoint returned JSON instead of file content.")
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise DiskAPIError(
                f"Download failed with HTTP {response.status_code}.",
                status_code=response.status_code,
            ) from exc

        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 128):
                if chunk:
                    handle.write(chunk)
        response.close()
        return response.url

    def get_upload_token(self, parent_id: str, filename: str) -> UploadToken:
        body = self.request(
            "GET",
            f"/api/res/{parent_id}/token",
            params={"filename": filename},
        )
        if not isinstance(body, dict):
            raise DiskAPIError("Upload-token endpoint returned an unexpected payload.")
        return UploadToken(key=body["key"], upload_token=body["upload_token"])

    def upload_file(self, upload: UploadToken, local_path: Path) -> dict[str, Any]:
        with local_path.open("rb") as handle:
            body = self.request(
                "POST",
                "",
                absolute_url=self.settings.qiniu_upload_url,
                include_auth=False,
                data={"key": upload.key, "token": upload.upload_token},
                files={"file": (local_path.name, handle)},
            )
        if not isinstance(body, dict):
            raise DiskAPIError("Upload endpoint returned an unexpected payload.")
        return body
