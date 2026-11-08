from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time
from typing import Any, Callable, Dict, Optional
from urllib.parse import urljoin, urlparse

import requests
from qiniu import Auth
from qiniu.services.storage.uploader import ResumeUploaderV2, UploadProgressRecorder

from .config import PockloSettings


class PockloAPIError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        identifier: Optional[str] = None,
        debug_msg: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.identifier = identifier
        self.debug_msg = debug_msg


@dataclass
class UploadToken:
    key: str
    upload_token: str


ProgressCallback = Callable[[int, Optional[int]], None]
UploadPhaseCallback = Callable[[str], None]
MAX_RETRIES = 5
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class PockloClient:
    def __init__(self, settings: PockloSettings, *, timeout: float = 30.0) -> None:
        self.settings = settings
        self.timeout = timeout
        self.session = requests.Session()

    def _headers(self, *, include_auth: bool = True) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        if include_auth and self.settings.token:
            headers["Token"] = self.settings.token
        return headers

    def _unwrap_response(self, response: requests.Response) -> Any:
        try:
            payload = response.json()
        except ValueError as exc:
            raise PockloAPIError(
                f"Server returned a non-JSON response (HTTP {response.status_code})."
            ) from exc

        return self._unwrap_payload(payload, status_code=response.status_code)

    @staticmethod
    def _unwrap_payload(payload: Any, *, status_code: Optional[int] = None) -> Any:
        if not isinstance(payload, dict) or "code" not in payload:
            return payload

        if payload.get("code") != 0:
            raise PockloAPIError(
                payload.get("msg") or "The Pocklo API request failed.",
                status_code=status_code,
                identifier=payload.get("identifier"),
                debug_msg=payload.get("debug_msg"),
            )

        return payload.get("body")

    @staticmethod
    def _retry_delay(attempt: int) -> float:
        return min(0.5 * (2 ** (attempt - 1)), 4.0)

    @staticmethod
    def _should_retry_api_error(error: PockloAPIError) -> bool:
        return error.status_code in RETRYABLE_STATUS_CODES

    def _request_once(
        self,
        method: str,
        url: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        files: Optional[Dict[str, Any]] = None,
        include_auth: bool = True,
    ) -> Any:
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

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        absolute_url: Optional[str] = None,
        include_auth: bool = True,
        data: Optional[Dict[str, Any]] = None,
        files: Optional[Dict[str, Any]] = None,
    ) -> Any:
        url = absolute_url or urljoin(f"{self.settings.backend_url}/", path.lstrip("/"))
        last_error: Optional[Exception] = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                return self._request_once(
                    method,
                    url,
                    params=params,
                    json=json,
                    data=data,
                    files=files,
                    include_auth=include_auth,
                )
            except PockloAPIError as exc:
                last_error = exc
                if attempt >= MAX_RETRIES or not self._should_retry_api_error(exc):
                    raise
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= MAX_RETRIES:
                    raise PockloAPIError(
                        f"Request failed after {MAX_RETRIES} attempts: {exc}"
                    ) from exc
            time.sleep(self._retry_delay(attempt))

        if isinstance(last_error, PockloAPIError):
            raise last_error
        raise PockloAPIError(f"Request failed after {MAX_RETRIES} attempts.")

    def exchange_code(self, code: str) -> Dict[str, Any]:
        body = self.request(
            "GET",
            "/api/oauth/qtb/callback",
            params={"code": code},
            include_auth=False,
        )
        if not isinstance(body, dict) or "token" not in body:
            raise PockloAPIError("OAuth callback did not return a login token.")
        self.settings.token = body["token"]
        return body

    def get_current_user(self) -> Dict[str, Any]:
        body = self.request("GET", "/api/user/")
        if not isinstance(body, dict):
            raise PockloAPIError("User endpoint returned an unexpected payload.")
        return body

    def get_resource(self, res_str_id: str, visit_key: Optional[str] = None) -> Dict[str, Any]:
        params = {"visit_key": visit_key} if visit_key else None
        body = self.request("GET", f"/api/res/{res_str_id}", params=params)
        if not isinstance(body, dict):
            raise PockloAPIError("Resource endpoint returned an unexpected payload.")
        return body

    def get_selector(self, res_str_id: str) -> Dict[str, Any]:
        body = self.request("GET", f"/api/res/{res_str_id}/selector")
        if not isinstance(body, dict):
            raise PockloAPIError("Selector endpoint returned an unexpected payload.")
        return body

    def get_path(self, res_str_id: str) -> list[str]:
        body = self.request("GET", f"/api/res/{res_str_id}/path")
        if not isinstance(body, list):
            raise PockloAPIError("Path endpoint returned an unexpected payload.")
        return [str(item) for item in body]

    def create_folder(self, parent_id: str, folder_name: str) -> Dict[str, Any]:
        body = self.request(
            "POST",
            f"/api/res/{parent_id}/folder",
            json={"folder_name": folder_name},
        )
        if not isinstance(body, dict):
            raise PockloAPIError("Create-folder endpoint returned an unexpected payload.")
        return body

    def create_link(self, parent_id: str, link_name: str, link: str) -> Dict[str, Any]:
        body = self.request(
            "POST",
            f"/api/res/{parent_id}/link",
            json={"link_name": link_name, "link": link},
        )
        if not isinstance(body, dict):
            raise PockloAPIError("Create-link endpoint returned an unexpected payload.")
        return body

    def update_resource(
        self,
        res_str_id: str,
        *,
        rname: Optional[str] = None,
        status: Optional[int] = None,
        description: Optional[str] = None,
        visit_key: Optional[str] = None,
        right_bubble: Optional[bool] = None,
        parent_str_id: Optional[str] = None,
    ) -> Dict[str, Any]:
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
            raise PockloAPIError("Update-resource endpoint returned an unexpected payload.")
        return body

    def delete_resource(self, res_str_id: str) -> None:
        self.request("DELETE", f"/api/res/{res_str_id}")

    def _is_backend_json_api_response(
        self,
        response: requests.Response,
        *,
        requested_url: str,
    ) -> bool:
        content_type = response.headers.get("content-type", "").lower()
        if "application/json" not in content_type:
            return False

        content_disposition = response.headers.get("content-disposition", "").lower()
        if "attachment" in content_disposition or "filename=" in content_disposition:
            return False

        final_url = urlparse(response.url)
        backend_url = urlparse(self.settings.backend_url)
        requested = urlparse(requested_url)
        if (final_url.scheme, final_url.netloc) != (backend_url.scheme, backend_url.netloc):
            return False
        return final_url.path.rstrip("/") == requested.path.rstrip("/")

    def download_resource(
        self,
        res_str_id: str,
        destination: Path,
        *,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> str:
        url = urljoin(f"{self.settings.backend_url}/", f"/api/res/{res_str_id}/dl".lstrip("/"))
        last_error: Optional[Exception] = None
        for attempt in range(1, MAX_RETRIES + 1):
            response: Optional[requests.Response] = None
            try:
                response = self.session.get(
                    url,
                    params={"token": self.settings.token or ""},
                    headers={},
                    timeout=self.timeout,
                    stream=True,
                    allow_redirects=True,
                )
                try:
                    response.raise_for_status()
                except requests.HTTPError as exc:
                    raise PockloAPIError(
                        f"Download failed with HTTP {response.status_code}.",
                        status_code=response.status_code,
                    ) from exc

                if self._is_backend_json_api_response(response, requested_url=url):
                    self._unwrap_response(response)
                    raise PockloAPIError(
                        "Download endpoint returned JSON metadata instead of file content."
                    )

                total_header = response.headers.get("content-length")
                total = int(total_header) if total_header and total_header.isdigit() else None
                if progress_callback:
                    progress_callback(0, total)

                destination.parent.mkdir(parents=True, exist_ok=True)
                transferred = 0
                with destination.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=1024 * 128):
                        if chunk:
                            handle.write(chunk)
                            transferred += len(chunk)
                            if progress_callback:
                                progress_callback(transferred, total)
                return response.url
            except PockloAPIError as exc:
                last_error = exc
                if attempt >= MAX_RETRIES or not self._should_retry_api_error(exc):
                    raise
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= MAX_RETRIES:
                    raise PockloAPIError(
                        f"Download failed after {MAX_RETRIES} attempts: {exc}"
                    ) from exc
            finally:
                if response is not None:
                    response.close()
            time.sleep(self._retry_delay(attempt))

        if isinstance(last_error, PockloAPIError):
            raise last_error
        raise PockloAPIError(f"Download failed after {MAX_RETRIES} attempts.")

    def get_upload_token(self, parent_id: str, filename: str) -> UploadToken:
        body = self.request(
            "GET",
            f"/api/res/{parent_id}/token",
            params={"filename": filename},
        )
        if not isinstance(body, dict):
            raise PockloAPIError("Upload-token endpoint returned an unexpected payload.")
        return UploadToken(key=body["key"], upload_token=body["upload_token"])

    def upload_file(
        self,
        upload: UploadToken,
        local_path: Path,
        *,
        progress_callback: Optional[ProgressCallback] = None,
        phase_callback: Optional[UploadPhaseCallback] = None,
    ) -> Dict[str, Any]:
        last_error: Optional[Exception] = None
        file_size = local_path.stat().st_size
        bucket_name = Auth.get_bucket_name(upload.upload_token)
        if not bucket_name:
            raise PockloAPIError("Upload token does not contain a valid bucket name.")

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                self.settings.upload_record_dir.mkdir(parents=True, exist_ok=True)
                recorder = UploadProgressRecorder(str(self.settings.upload_record_dir))
                uploader = ResumeUploaderV2(
                    bucket_name,
                    part_size=4 * 1024 * 1024,
                    progress_handler=progress_callback,
                    upload_progress_recorder=recorder,
                )

                context, resp = uploader.initial_parts(
                    up_token=upload.upload_token,
                    key=upload.key,
                    file_path=str(local_path),
                    file_name=local_path.name,
                )
                if resp is not None and not resp.ok():
                    raise PockloAPIError(
                        f"Failed to initialize multipart upload: {resp.error}",
                        status_code=resp.status_code,
                        debug_msg=resp.text_body,
                    )

                with local_path.open("rb") as file_handle:
                    _, resp = uploader.upload_parts(
                        up_token=upload.upload_token,
                        data=file_handle,
                        data_size=file_size,
                        context=context,
                        key=upload.key,
                        file_name=local_path.name,
                    )
                if resp is not None and not resp.ok():
                    raise PockloAPIError(
                        f"Failed while uploading file parts: {resp.error}",
                        status_code=resp.status_code,
                        debug_msg=resp.text_body,
                    )

                if phase_callback:
                    phase_callback("finalizing")

                body, resp = uploader.complete_parts(
                    up_token=upload.upload_token,
                    data_size=file_size,
                    context=context,
                    key=upload.key,
                    file_name=local_path.name,
                )
                if resp is not None and not resp.ok():
                    raise PockloAPIError(
                        f"Failed to finalize multipart upload: {resp.error}",
                        status_code=resp.status_code,
                        debug_msg=resp.text_body,
                    )
                body = self._unwrap_payload(body, status_code=resp.status_code if resp is not None else None)
                if not isinstance(body, dict):
                    raise PockloAPIError("Upload endpoint returned an unexpected payload.")
                return body
            except PockloAPIError as exc:
                last_error = exc
                if attempt >= MAX_RETRIES or not self._should_retry_api_error(exc):
                    raise
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= MAX_RETRIES:
                    raise PockloAPIError(
                        f"Upload failed after {MAX_RETRIES} attempts: {exc}"
                    ) from exc
            time.sleep(self._retry_delay(attempt))

        if isinstance(last_error, PockloAPIError):
            raise last_error
        raise PockloAPIError(f"Upload failed after {MAX_RETRIES} attempts.")
