import mimetypes
import os
import tempfile
import uuid
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import requests

from core.proxy_utils import build_requests_proxies, resolve_resource_proxy


class ImgBedUploadError(Exception):
    pass


class ImgBedClient:
    def __init__(self) -> None:
        self.enabled = False
        self.api_url = ""
        self.api_key = ""
        self.resource_proxy = ""
        self.timeout = 300

    def apply_config(self, cfg: dict) -> None:
        self.enabled = bool(cfg.get("imgbed_enabled", False))
        self.api_url = str(cfg.get("imgbed_api_url", "") or "").strip()
        self.api_key = str(cfg.get("imgbed_api_key", "") or "").strip()
        self.resource_proxy = resolve_resource_proxy(cfg)
        timeout_val = cfg.get("generate_timeout", 300)
        try:
            timeout_val = int(timeout_val)
        except Exception:
            timeout_val = 300
        self.timeout = timeout_val if timeout_val > 0 else 300

    def is_enabled(self) -> bool:
        return self.enabled

    def is_ready(self) -> bool:
        return self.enabled and bool(self.api_url) and bool(self.api_key)

    def _requests_proxies(self) -> dict | None:
        return build_requests_proxies(self.resource_proxy)

    def _build_upload_url(self) -> str:
        raw = str(self.api_url or "").strip()
        if not raw:
            raise ImgBedUploadError("imgbed_api_url is empty")
        parsed = urlparse(raw)
        if parsed.scheme not in {"http", "https"}:
            raise ImgBedUploadError("imgbed_api_url must start with http:// or https://")
        if not self.api_key:
            raise ImgBedUploadError("imgbed_api_key is empty")
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query["authCode"] = self.api_key
        query["returnFormat"] = "full"
        return urlunparse(parsed._replace(query=urlencode(query)))

    def _parse_response_url(self, payload) -> str:
        src = ""
        if isinstance(payload, list) and payload:
            first = payload[0] if isinstance(payload[0], dict) else {}
            src = str(first.get("src") or first.get("url") or "").strip()
        elif isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, list) and data:
                first = data[0] if isinstance(data[0], dict) else {}
                src = str(first.get("src") or first.get("url") or "").strip()
            elif isinstance(data, dict):
                src = str(data.get("src") or data.get("url") or "").strip()
            if not src:
                src = str(payload.get("src") or payload.get("url") or "").strip()
        if not src:
            raise ImgBedUploadError("imgbed upload succeeded but no file url returned")
        if src.startswith(("http://", "https://")):
            return src
        parsed = urlparse(self.api_url)
        base = f"{parsed.scheme}://{parsed.netloc}/"
        return urljoin(base, src.lstrip("/"))

    def upload_bytes(
        self, filename: str, content: bytes, mime_type: str | None = None
    ) -> str:
        if not content:
            raise ImgBedUploadError("imgbed upload content is empty")
        safe_name = str(filename or "").strip() or f"{uuid.uuid4().hex}.bin"
        guessed_type = mime_type or mimetypes.guess_type(safe_name)[0]
        upload_url = self._build_upload_url()
        try:
            resp = requests.post(
                upload_url,
                files={
                    "file": (
                        safe_name,
                        content,
                        guessed_type or "application/octet-stream",
                    )
                },
                timeout=self.timeout,
                proxies=self._requests_proxies(),
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise ImgBedUploadError(f"imgbed upload failed: {exc}") from exc
        try:
            payload = resp.json()
        except Exception as exc:
            raise ImgBedUploadError("imgbed upload returned invalid JSON") from exc
        return self._parse_response_url(payload)

    def upload_file(
        self, file_path: Path, filename: str | None = None, mime_type: str | None = None
    ) -> str:
        path = Path(file_path)
        if not path.exists() or not path.is_file():
            raise ImgBedUploadError("imgbed upload file not found")
        safe_name = str(filename or path.name).strip() or path.name
        guessed_type = mime_type or mimetypes.guess_type(safe_name)[0]
        upload_url = self._build_upload_url()
        try:
            with path.open("rb") as f:
                resp = requests.post(
                    upload_url,
                    files={
                        "file": (
                            safe_name,
                            f,
                            guessed_type or "application/octet-stream",
                        )
                    },
                    timeout=self.timeout,
                    proxies=self._requests_proxies(),
                )
                resp.raise_for_status()
        except requests.RequestException as exc:
            raise ImgBedUploadError(f"imgbed upload failed: {exc}") from exc
        try:
            payload = resp.json()
        except Exception as exc:
            raise ImgBedUploadError("imgbed upload returned invalid JSON") from exc
        return self._parse_response_url(payload)

    def upload_from_url(
        self, source_url: str, filename: str, mime_type: str | None = None
    ) -> str:
        raw_url = str(source_url or "").strip()
        if not raw_url.startswith(("http://", "https://")):
            raise ImgBedUploadError("imgbed source url must start with http:// or https://")
        suffix = Path(str(filename or "")).suffix or Path(urlparse(raw_url).path).suffix
        temp_path = None
        try:
            with requests.get(
                raw_url,
                timeout=self.timeout,
                proxies=self._requests_proxies(),
                stream=True,
            ) as resp:
                resp.raise_for_status()
                guessed_type = mime_type or (
                    (resp.headers.get("content-type") or "").split(";", 1)[0].strip()
                    or None
                )
                with tempfile.NamedTemporaryFile(
                    delete=False, suffix=suffix or ".bin"
                ) as tmp:
                    temp_path = Path(tmp.name)
                    for chunk in resp.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            tmp.write(chunk)
            return self.upload_file(
                temp_path,
                filename=filename,
                mime_type=guessed_type,
            )
        except requests.RequestException as exc:
            raise ImgBedUploadError(f"imgbed source download failed: {exc}") from exc
        finally:
            if temp_path is not None:
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
