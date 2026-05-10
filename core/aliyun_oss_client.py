import base64
import email.utils
import hmac
import mimetypes
import os
import tempfile
import time
import uuid
from hashlib import sha1
from pathlib import Path
from urllib.parse import quote, urlparse

import requests
from requests import exceptions as requests_exceptions

from core.proxy_utils import build_requests_proxies, resolve_resource_proxy


class AliyunOssUploadError(Exception):
    pass


class AliyunOssClient:
    def __init__(self) -> None:
        self.enabled = False
        self.endpoint = ""
        self.bucket = ""
        self.access_key_id = ""
        self.access_key_secret = ""
        self.security_token = ""
        self.prefix = ""
        self.public_base_url = ""
        self.acl = ""
        self.resource_proxy = ""
        self.timeout = 300

    def apply_config(self, cfg: dict) -> None:
        self.enabled = bool(cfg.get("aliyun_oss_enabled", False))
        self.endpoint = str(cfg.get("aliyun_oss_endpoint", "") or "").strip()
        self.bucket = str(cfg.get("aliyun_oss_bucket", "") or "").strip()
        self.access_key_id = str(
            cfg.get("aliyun_oss_access_key_id", "") or ""
        ).strip()
        self.access_key_secret = str(
            cfg.get("aliyun_oss_access_key_secret", "") or ""
        ).strip()
        self.security_token = str(
            cfg.get("aliyun_oss_security_token", "") or ""
        ).strip()
        self.prefix = str(cfg.get("aliyun_oss_prefix", "") or "").strip()
        self.public_base_url = str(
            cfg.get("aliyun_oss_public_base_url", "") or ""
        ).strip()
        self.acl = str(cfg.get("aliyun_oss_acl", "") or "").strip().lower()
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
        return (
            self.enabled
            and bool(self.endpoint)
            and bool(self.bucket)
            and bool(self.access_key_id)
            and bool(self.access_key_secret)
        )

    def _requests_proxies(self) -> dict | None:
        return build_requests_proxies(self.resource_proxy)

    def _validate_ready(self) -> None:
        if not self.endpoint.startswith(("http://", "https://")):
            raise AliyunOssUploadError(
                "aliyun_oss_endpoint must start with http:// or https://"
            )
        if not self.bucket:
            raise AliyunOssUploadError("aliyun_oss_bucket is empty")
        if not self.access_key_id:
            raise AliyunOssUploadError("aliyun_oss_access_key_id is empty")
        if not self.access_key_secret:
            raise AliyunOssUploadError("aliyun_oss_access_key_secret is empty")
        if self.acl and self.acl not in {"private", "public-read", "public-read-write"}:
            raise AliyunOssUploadError("aliyun_oss_acl is invalid")

    def _object_key(self, filename: str) -> str:
        safe_name = str(filename or "").strip() or f"{uuid.uuid4().hex}.bin"
        safe_name = safe_name.replace("\\", "/").lstrip("/")
        prefix = str(self.prefix or "").strip().replace("\\", "/").strip("/")
        if prefix:
            return f"{prefix}/{safe_name}"
        return safe_name

    def _upload_url(self, object_key: str) -> str:
        parsed = urlparse(self.endpoint.rstrip("/"))
        host = parsed.netloc
        if not host:
            raise AliyunOssUploadError("aliyun_oss_endpoint is invalid")
        path_prefix = parsed.path.rstrip("/")
        encoded_key = quote(object_key, safe="/")
        return f"{parsed.scheme}://{self.bucket}.{host}{path_prefix}/{encoded_key}"

    def _public_url(self, object_key: str) -> str:
        encoded_key = quote(object_key, safe="/")
        if self.public_base_url:
            return f"{self.public_base_url.rstrip('/')}/{encoded_key}"
        return self._upload_url(object_key)

    def _authorization(
        self, method: str, object_key: str, content_type: str, headers: dict[str, str]
    ) -> str:
        oss_headers = {
            key.lower(): str(value).strip()
            for key, value in headers.items()
            if key.lower().startswith("x-oss-") and str(value).strip()
        }
        canonical_headers = "".join(
            f"{key}:{oss_headers[key]}\n" for key in sorted(oss_headers)
        )
        resource = f"/{self.bucket}/{object_key}"
        date = headers["Date"]
        string_to_sign = "\n".join(
            [
                method.upper(),
                "",
                content_type,
                date,
                f"{canonical_headers}{resource}",
            ]
        )
        digest = hmac.new(
            self.access_key_secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            sha1,
        ).digest()
        signature = base64.b64encode(digest).decode("ascii")
        return f"OSS {self.access_key_id}:{signature}"

    def _put_object(self, object_key: str, content, content_type: str) -> str:
        headers = {
            "Date": email.utils.formatdate(time.time(), usegmt=True),
            "Content-Type": content_type,
        }
        if self.security_token:
            headers["x-oss-security-token"] = self.security_token
        if self.acl:
            headers["x-oss-object-acl"] = self.acl
        headers["Authorization"] = self._authorization(
            "PUT", object_key, content_type, headers
        )
        try:
            resp = requests.put(
                self._upload_url(object_key),
                data=content,
                headers=headers,
                timeout=self.timeout,
                proxies=self._requests_proxies(),
            )
            resp.raise_for_status()
        except requests_exceptions.RequestException as exc:
            raise AliyunOssUploadError(f"aliyun oss upload failed: {exc}") from exc
        return self._public_url(object_key)

    def upload_bytes(
        self, filename: str, content: bytes, mime_type: str | None = None
    ) -> str:
        if not content:
            raise AliyunOssUploadError("aliyun oss upload content is empty")
        self._validate_ready()
        object_key = self._object_key(filename)
        content_type = (
            mime_type
            or mimetypes.guess_type(str(filename or ""))[0]
            or "application/octet-stream"
        )
        return self._put_object(object_key, content, content_type)

    def upload_file(
        self, file_path: Path, filename: str | None = None, mime_type: str | None = None
    ) -> str:
        path = Path(file_path)
        if not path.exists() or not path.is_file():
            raise AliyunOssUploadError("aliyun oss upload file not found")
        safe_name = str(filename or path.name).strip() or path.name
        self._validate_ready()
        object_key = self._object_key(safe_name)
        content_type = (
            mime_type
            or mimetypes.guess_type(safe_name)[0]
            or "application/octet-stream"
        )
        with path.open("rb") as f:
            return self._put_object(object_key, f, content_type)


    def upload_from_url(
        self, source_url: str, filename: str, mime_type: str | None = None
    ) -> str:
        raw_url = str(source_url or "").strip()
        if not raw_url.startswith(("http://", "https://")):
            raise AliyunOssUploadError(
                "aliyun oss source url must start with http:// or https://"
            )
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
        except requests_exceptions.RequestException as exc:
            raise AliyunOssUploadError(
                f"aliyun oss source download failed: {exc}"
            ) from exc
        finally:
            if temp_path is not None:
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
