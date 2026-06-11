import base64
import hashlib
import json
import logging
import os
import random
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse

import requests
from requests import exceptions as requests_exceptions

from core.config_mgr import config_manager
from core.models import build_image_payload_candidates
from core.proxy_utils import build_requests_proxies, resolve_basic_proxy, resolve_resource_proxy

try:
    from curl_cffi.requests import Session as CurlSession
except Exception:
    CurlSession = None


logger = logging.getLogger("adobe2api")


def _build_arp_session_id() -> str:
    now_ms = int(time.time() * 1000)
    ftr = f"{os.urandom(16).hex()}_{now_ms}_{os.getpid()}_dUAL43-mnts-ants-d4_31ck__tt"
    raw = json.dumps(
        {"sid": str(uuid.uuid4()), "ftr": ftr},
        separators=(",", ":"),
    )
    return base64.b64encode(raw.encode("utf-8")).decode("ascii")


class AdobeRequestError(Exception):
    pass


class QuotaExhaustedError(AdobeRequestError):
    pass


class AuthError(AdobeRequestError):
    pass


class UpstreamTemporaryError(AdobeRequestError):
    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        error_type: str = "",
    ):
        super().__init__(message)
        self.status_code = status_code
        self.error_type = str(error_type or "").strip().lower()


class AdobeClient:
    submit_url = "https://firefly-3p.ff.adobe.io/v2/3p-images/generate-async"
    video_submit_url = "https://firefly-3p.ff.adobe.io/v2/3p-videos/generate-async"
    upload_url = "https://firefly-3p.ff.adobe.io/v2/storage/image"

    def __init__(self) -> None:
        self.api_key = "clio-playground-web"
        self.impersonate = "chrome124"
        self.basic_proxy = ""
        self.resource_proxy = ""
        self.generate_timeout = 300
        self.retry_enabled = True
        self.retry_max_attempts = 3
        self.retry_backoff_seconds = 1.0
        self.retry_on_status_codes = [429, 451, 500, 502, 503, 504]
        self.retry_on_error_types = {"timeout", "connection", "proxy"}
        self.token_rotation_strategy = "round_robin"
        self.token_concurrency = 1
        self.token_success_auto_disable_enabled = False
        self.token_success_auto_disable_threshold = 2
        self.user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
        self.sec_ch_ua = (
            '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"'
        )

        self.apply_config(config_manager.get_all())

        env_api_key = os.getenv("ADOBE_API_KEY")
        env_impersonate = os.getenv("ADOBE_IMPERSONATE")
        env_proxy = os.getenv("ADOBE_PROXY")
        env_resource_proxy = os.getenv("ADOBE_RESOURCE_PROXY")
        env_user_agent = os.getenv("ADOBE_USER_AGENT")
        env_sec_ch_ua = os.getenv("ADOBE_SEC_CH_UA")
        env_generate_timeout = os.getenv("ADOBE_GENERATE_TIMEOUT")

        if env_api_key:
            self.api_key = env_api_key.strip() or self.api_key
        if env_impersonate:
            self.impersonate = env_impersonate.strip() or self.impersonate
        if env_proxy is not None:
            self.basic_proxy = env_proxy.strip()
        if env_resource_proxy is not None:
            self.resource_proxy = env_resource_proxy.strip()
        elif env_proxy is not None:
            self.resource_proxy = env_proxy.strip()
        if env_user_agent:
            self.user_agent = env_user_agent.strip() or self.user_agent
        if env_sec_ch_ua:
            self.sec_ch_ua = env_sec_ch_ua.strip() or self.sec_ch_ua
        if env_generate_timeout:
            try:
                self.generate_timeout = int(env_generate_timeout)
                if self.generate_timeout <= 0:
                    self.generate_timeout = 300
            except Exception:
                pass

    def apply_config(self, cfg: dict) -> None:
        self.basic_proxy = resolve_basic_proxy(cfg)
        self.resource_proxy = resolve_resource_proxy(cfg)
        timeout_val = cfg.get("generate_timeout", 300)
        try:
            timeout_val = int(timeout_val)
        except Exception:
            timeout_val = 300
        self.generate_timeout = timeout_val if timeout_val > 0 else 300
        self.retry_enabled = bool(cfg.get("retry_enabled", True))
        try:
            attempts = int(cfg.get("retry_max_attempts", 3))
        except Exception:
            attempts = 3
        self.retry_max_attempts = max(1, min(attempts, 10))

        try:
            backoff = float(cfg.get("retry_backoff_seconds", 1.0))
        except Exception:
            backoff = 1.0
        self.retry_backoff_seconds = max(0.0, min(backoff, 30.0))

        status_codes_raw = cfg.get(
            "retry_on_status_codes", [429, 451, 500, 502, 503, 504]
        )
        parsed_status_codes: list[int] = []
        if isinstance(status_codes_raw, list):
            for item in status_codes_raw:
                try:
                    val = int(item)
                except Exception:
                    continue
                if 100 <= val <= 599:
                    parsed_status_codes.append(val)
        self.retry_on_status_codes = sorted(set(parsed_status_codes)) or [
            429,
            451,
            500,
            502,
            503,
            504,
        ]

        error_types_raw = cfg.get(
            "retry_on_error_types", ["timeout", "connection", "proxy"]
        )
        parsed_error_types: set[str] = set()
        if isinstance(error_types_raw, list):
            for item in error_types_raw:
                txt = str(item or "").strip().lower()
                if txt:
                    parsed_error_types.add(txt)
        self.retry_on_error_types = parsed_error_types or {
            "timeout",
            "connection",
            "proxy",
        }

        strategy = (
            str(cfg.get("token_rotation_strategy", "round_robin") or "round_robin")
            .strip()
            .lower()
        )
        if strategy not in {"round_robin", "random", "finish_success"}:
            strategy = "round_robin"
        self.token_rotation_strategy = strategy
        try:
            token_concurrency = int(cfg.get("token_concurrency", 1))
        except Exception:
            token_concurrency = 1
        self.token_concurrency = max(1, min(token_concurrency, 10))
        self.token_success_auto_disable_enabled = bool(
            cfg.get("token_success_auto_disable_enabled", False)
        )
        try:
            threshold = int(cfg.get("token_success_auto_disable_threshold", 2))
        except Exception:
            threshold = 2
        self.token_success_auto_disable_threshold = max(1, min(threshold, 100000))
        if self.basic_proxy:
            logger.warning(
                "basic proxy enabled for upstream requests: %s", self.basic_proxy
            )
        else:
            logger.warning("basic proxy disabled for upstream requests")
        if self.resource_proxy:
            logger.warning(
                "resource proxy enabled for media transfer requests: %s",
                self.resource_proxy,
            )
        else:
            logger.warning("resource proxy disabled for media transfer requests")

    def _retry_delay_for_attempt(self, attempt: int) -> float:
        base = float(self.retry_backoff_seconds or 0.0)
        if base <= 0:
            return 0.0
        safe_attempt = max(1, int(attempt))
        return min(30.0, base * (2 ** (safe_attempt - 1)))

    def should_retry_temporary_error(self, exc: UpstreamTemporaryError) -> bool:
        if not self.retry_enabled:
            return False
        if isinstance(exc, UpstreamTemporaryError):
            if exc.status_code is not None:
                try:
                    return int(exc.status_code) in set(self.retry_on_status_codes)
                except Exception:
                    return False
            if exc.error_type:
                return exc.error_type in set(self.retry_on_error_types)
        return False

    @staticmethod
    def _classify_network_error_type(exc: Exception) -> str:
        text = str(exc or "").strip().lower()
        if (
            "curl: (16)" in text
            or "http2" in text
            or "framing layer" in text
            or "stream error" in text
        ):
            return "connection"
        if "timed out" in text or "timeout" in text:
            return "timeout"
        if "proxy" in text:
            return "proxy"
        if (
            "connection" in text
            or "dns" in text
            or "resolve" in text
            or "refused" in text
            or "reset" in text
            or "unreachable" in text
        ):
            return "connection"
        return "network"

    def _requests_proxies(self, proxy_kind: str = "basic") -> Optional[dict]:
        proxy = (
            self.resource_proxy if str(proxy_kind).strip().lower() == "resource" else self.basic_proxy
        )
        return build_requests_proxies(proxy)

    def _session(self, proxy_kind: str = "basic"):
        if CurlSession is None:
            return None
        kwargs = {"impersonate": self.impersonate, "timeout": 60}
        proxies = self._requests_proxies(proxy_kind=proxy_kind)
        if proxies:
            kwargs["proxies"] = proxies
        return CurlSession(**kwargs)

    def _requests_request(
        self,
        method: str,
        url: str,
        *,
        headers: dict,
        timeout: int = 60,
        proxy_kind: str = "basic",
        request_name: str = "request",
        json_payload: Optional[dict] = None,
        data_payload: Optional[bytes] = None,
    ):
        request_kwargs = {
            "headers": headers,
            "timeout": timeout,
            "proxies": self._requests_proxies(proxy_kind=proxy_kind),
        }
        if json_payload is not None:
            request_kwargs["json"] = json_payload
        if data_payload is not None:
            request_kwargs["data"] = data_payload
        try:
            if method == "post":
                return requests.post(url, **request_kwargs)
            if method == "get":
                return requests.get(url, **request_kwargs)
            raise ValueError(f"unsupported request method: {method}")
        except requests_exceptions.Timeout as exc:
            raise UpstreamTemporaryError(
                f"{request_name} upstream timeout via requests: {exc}",
                error_type="timeout",
            )
        except requests_exceptions.ProxyError as exc:
            raise UpstreamTemporaryError(
                f"{request_name} upstream proxy error via requests: {exc}",
                error_type="proxy",
            )
        except requests_exceptions.ConnectionError as exc:
            raise UpstreamTemporaryError(
                f"{request_name} upstream connection error via requests: {exc}",
                error_type="connection",
            )
        except requests_exceptions.RequestException as exc:
            raise UpstreamTemporaryError(
                f"{request_name} upstream request error via requests: {exc}",
                error_type="network",
            )

    def _browser_headers(self) -> dict:
        return {
            "user-agent": self.user_agent,
            "origin": "https://firefly.adobe.com",
            "referer": "https://firefly.adobe.com/",
            "accept-language": "en-US,en;q=0.9",
            "sec-ch-ua": self.sec_ch_ua,
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-site": "cross-site",
            "sec-fetch-mode": "cors",
            "sec-fetch-dest": "empty",
        }

    @staticmethod
    def _extract_user_id_from_token(token: str) -> str:
        try:
            parts = str(token or "").split(".")
            if len(parts) < 2:
                return ""
            payload_b64 = parts[1]
            padding = 4 - len(payload_b64) % 4
            if padding != 4:
                payload_b64 += "=" * padding
            import base64

            jwt = json.loads(base64.urlsafe_b64decode(payload_b64))
            return str(jwt.get("user_id") or "")
        except Exception:
            return ""

    @staticmethod
    def _compute_nonce(user_id: str, prompt: str) -> str:
        prompt_text = str(prompt or "")
        return hashlib.sha256(
            f"{str(user_id or '')}-{prompt_text[:256]}".encode("utf-8")
        ).hexdigest()

    def _submit_headers(self, token: str, prompt: str = "") -> dict:
        headers = self._browser_headers()
        user_id = self._extract_user_id_from_token(token)
        headers.update(
            {
                "Authorization": f"Bearer {token}",
                "x-api-key": self.api_key,
                "x-nonce": self._compute_nonce(user_id, prompt),
                "x-arp-session-id": _build_arp_session_id(),
                "content-type": "application/json",
                "accept": "*/*",
            }
        )
        return headers

    def _submit_headers_minimal(self, token: str, prompt: str = "") -> dict:
        user_id = self._extract_user_id_from_token(token)
        return {
            "Authorization": f"Bearer {token}",
            "x-api-key": self.api_key,
            "x-nonce": self._compute_nonce(user_id, prompt),
            "x-arp-session-id": _build_arp_session_id(),
            "content-type": "application/json",
            "accept": "*/*",
        }

    def _poll_headers(self, token: str) -> dict:
        return {
            "Authorization": f"Bearer {token}",
            "x-api-key": self.api_key,
            "accept": "*/*",
            "referer": "https://firefly.adobe.com/",
            "origin": "https://firefly.adobe.com",
            "user-agent": self.user_agent,
            "sec-ch-ua": self.sec_ch_ua,
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-site": "cross-site",
            "sec-fetch-mode": "cors",
            "sec-fetch-dest": "empty",
        }

    @staticmethod
    def _raise_auth_or_quota(resp) -> None:
        access_error = str(resp.headers.get("x-access-error") or "").strip().lower()
        response_text = str(getattr(resp, "text", "") or "").casefold()
        if (
            access_error == "taste_exhausted"
            or "token quota exhausted" in response_text
        ):
            raise QuotaExhaustedError("Adobe quota exhausted for this account")
        raise AuthError("Token invalid or expired")

    def _post_json(
        self,
        url: str,
        headers: dict,
        payload: dict,
        request_name: str = "request",
    ):
        session = self._session(proxy_kind="basic")
        if session is None:
            return self._requests_request(
                "post",
                url,
                headers=headers,
                timeout=60,
                proxy_kind="basic",
                request_name=request_name,
                json_payload=payload,
            )
        try:
            with session:
                resp = session.post(url, headers=headers, json=payload)
        except Exception as exc:
            error_type = self._classify_network_error_type(exc)
            logger.warning(
                "%s failed via curl_cffi session; falling back to requests error_type=%s error=%s",
                request_name,
                error_type,
                str(exc),
            )
            return self._requests_request(
                "post",
                url,
                headers=headers,
                timeout=60,
                proxy_kind="basic",
                request_name=request_name,
                json_payload=payload,
            )
        if resp.status_code == 451:
            logger.warning(
                "%s returned status 451 via curl_cffi; retrying via requests",
                request_name,
            )
            return self._requests_request(
                "post",
                url,
                headers=headers,
                timeout=60,
                proxy_kind="basic",
                request_name=request_name,
                json_payload=payload,
            )
        return resp

    def _post_bytes(
        self,
        url: str,
        headers: dict,
        payload: bytes,
        proxy_kind: str = "basic",
        request_name: str = "request",
    ):
        session = self._session(proxy_kind=proxy_kind)
        if session is None:
            return self._requests_request(
                "post",
                url,
                headers=headers,
                timeout=60,
                proxy_kind=proxy_kind,
                request_name=request_name,
                data_payload=payload,
            )
        try:
            with session:
                resp = session.post(url, headers=headers, data=payload)
        except Exception as exc:
            error_type = self._classify_network_error_type(exc)
            logger.warning(
                "%s failed via curl_cffi session; falling back to requests error_type=%s error=%s",
                request_name,
                error_type,
                str(exc),
            )
            return self._requests_request(
                "post",
                url,
                headers=headers,
                timeout=60,
                proxy_kind=proxy_kind,
                request_name=request_name,
                data_payload=payload,
            )
        return resp

    def _get(
        self,
        url: str,
        headers: dict,
        timeout: int = 60,
        proxy_kind: str = "basic",
        request_name: str = "request",
    ):
        session = self._session(proxy_kind=proxy_kind)
        if session is None:
            return self._requests_request(
                "get",
                url,
                headers=headers,
                timeout=timeout,
                proxy_kind=proxy_kind,
                request_name=request_name,
            )
        try:
            with session:
                resp = session.get(url, headers=headers)
        except Exception as exc:
            error_type = self._classify_network_error_type(exc)
            logger.warning(
                "%s failed via curl_cffi session; falling back to requests error_type=%s error=%s",
                request_name,
                error_type,
                str(exc),
            )
            return self._requests_request(
                "get",
                url,
                headers=headers,
                timeout=timeout,
                proxy_kind=proxy_kind,
                request_name=request_name,
            )
        return resp

    def _download_to_file(
        self,
        url: str,
        headers: Optional[dict],
        out_path: Path,
        timeout: int = 60,
        chunk_size: int = 1024 * 1024,
    ) -> int:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        total = 0
        try:
            with requests.get(
                url,
                headers=headers or {},
                timeout=timeout,
                proxies=self._requests_proxies(proxy_kind="resource"),
                stream=True,
            ) as resp:
                resp.raise_for_status()
                with out_path.open("wb") as f:
                    for chunk in resp.iter_content(chunk_size=chunk_size):
                        if not chunk:
                            continue
                        f.write(chunk)
                        total += len(chunk)
        except requests_exceptions.Timeout as exc:
            raise UpstreamTemporaryError(f"upstream timeout: {exc}", error_type="timeout")
        except requests_exceptions.ProxyError as exc:
            raise UpstreamTemporaryError(
                f"upstream proxy error: {exc}", error_type="proxy"
            )
        except requests_exceptions.ConnectionError as exc:
            raise UpstreamTemporaryError(
                f"upstream connection error: {exc}", error_type="connection"
            )
        except requests_exceptions.RequestException as exc:
            raise UpstreamTemporaryError(f"upstream request error: {exc}", error_type="network")
        return total

    def upload_image(
        self, token: str, image_bytes: bytes, mime_type: str = "image/jpeg"
    ) -> str:
        if not image_bytes:
            raise AdobeRequestError("image is empty")

        headers = {
            "authorization": f"Bearer {token}",
            "x-api-key": self.api_key,
            "content-type": mime_type,
            "accept": "application/json",
        }
        resp = self._post_bytes(
            self.upload_url,
            headers=headers,
            payload=image_bytes,
            proxy_kind="resource",
            request_name="upload",
        )

        if resp.status_code in (401, 403):
            self._raise_auth_or_quota(resp)
        if resp.status_code != 200:
            if resp.status_code in (429, 451) or resp.status_code >= 500:
                raise UpstreamTemporaryError(
                    f"upload image failed: {resp.status_code} {resp.text[:300]}",
                    status_code=resp.status_code,
                    error_type="status",
                )
            raise AdobeRequestError(
                f"upload image failed: {resp.status_code} {resp.text[:300]}"
            )

        try:
            data = resp.json()
        except Exception:
            raise AdobeRequestError("upload image failed: invalid response")

        image_id = (((data.get("images") or [{}])[0]) or {}).get("id")
        if not image_id:
            raise AdobeRequestError("upload image succeeded but no image id returned")
        return str(image_id)

    def _build_payload_candidates(
        self,
        prompt: str,
        aspect_ratio: str,
        output_resolution: str,
        upstream_model_id: str,
        upstream_model_version: str,
        source_image_ids: Optional[list[str]] = None,
        payload_style: str = "banana",
        generation_metadata: Optional[dict] = None,
        generation_settings: Optional[dict] = None,
        model_specific_payload: Optional[dict] = None,
        seeds: Optional[list[int]] = None,
    ) -> list[dict]:
        return build_image_payload_candidates(
            prompt=prompt,
            aspect_ratio=aspect_ratio,
            output_resolution=output_resolution,
            upstream_model_id=upstream_model_id,
            upstream_model_version=upstream_model_version,
            source_image_ids=source_image_ids,
            payload_style=payload_style,
            generation_metadata=generation_metadata,
            generation_settings=generation_settings,
            model_specific_payload=model_specific_payload,
            seeds=seeds,
        )

    @staticmethod
    def _video_size(aspect_ratio: str, resolution: str = "720p") -> dict:
        res = str(resolution or "720p").lower()
        if res == "1080p":
            if aspect_ratio == "16:9":
                return {"width": 1920, "height": 1080}
            return {"width": 1080, "height": 1920}
        if aspect_ratio == "16:9":
            return {"width": 1280, "height": 720}
        return {"width": 720, "height": 1280}

    @staticmethod
    def _coerce_progress_percent(value: Any) -> Optional[float]:
        if value is None:
            return None

        val: Optional[float] = None
        if isinstance(value, (int, float)):
            val = float(value)
        elif isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            if text.endswith("%"):
                text = text[:-1].strip()
            try:
                val = float(text)
            except Exception:
                return None
        elif isinstance(value, dict):
            for key in (
                "progress",
                "percentage",
                "percent",
                "task_progress",
                "taskProgress",
                "value",
            ):
                nested = AdobeClient._coerce_progress_percent(value.get(key))
                if nested is not None:
                    return nested
            return None
        else:
            return None

        if val <= 1.0:
            val = val * 100.0
        if val < 0:
            return 0.0
        if val > 100:
            return 100.0
        return val

    @staticmethod
    def _is_in_progress_status(status_val: str) -> bool:
        return str(status_val or "").upper() in {
            "IN_PROGRESS",
            "RUNNING",
            "PROCESSING",
            "PENDING",
            "QUEUED",
            "STARTED",
        }

    @staticmethod
    def _normalize_task_status(value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        normalized = text.upper().replace("-", "_").replace(" ", "_")
        aliases = {
            "SUCCESS": "COMPLETED",
            "SUCCEEDED": "COMPLETED",
            "DONE": "COMPLETED",
            "COMPLETE": "COMPLETED",
            "FAILURE": "FAILED",
            "FAIL": "FAILED",
            "CANCELED": "CANCELLED",
        }
        return aliases.get(normalized, normalized)

    @staticmethod
    def _is_failed_status(status_val: str) -> bool:
        return AdobeClient._normalize_task_status(status_val) in {
            "FAILED",
            "CANCELLED",
            "ERROR",
        }

    @staticmethod
    def _has_error_signal(obj: Any) -> bool:
        if not isinstance(obj, dict):
            return False
        for key in (
            "error",
            "errors",
            "failure",
            "failureReason",
            "failure_reason",
            "cancelReason",
            "cancel_reason",
        ):
            raw = obj.get(key)
            if raw is None or raw is False:
                continue
            if isinstance(raw, str):
                if raw.strip():
                    return True
                continue
            if isinstance(raw, (list, tuple, dict, set)):
                if len(raw) > 0:
                    return True
                continue
            return True
        return False

    def _extract_task_status(self, latest: dict, poll_resp) -> str:
        if not isinstance(latest, dict):
            latest = {}

        task_obj = latest.get("task") if isinstance(latest.get("task"), dict) else {}
        result_obj = (
            latest.get("result") if isinstance(latest.get("result"), dict) else {}
        )
        meta_obj = latest.get("meta") if isinstance(latest.get("meta"), dict) else {}
        metadata_obj = (
            latest.get("metadata") if isinstance(latest.get("metadata"), dict) else {}
        )

        candidates: list[Any] = [
            latest.get("status"),
            latest.get("state"),
            latest.get("task_status"),
            latest.get("taskStatus"),
            task_obj.get("status"),
            task_obj.get("state"),
            task_obj.get("task_status"),
            task_obj.get("taskStatus"),
            result_obj.get("status"),
            result_obj.get("state"),
            result_obj.get("task_status"),
            result_obj.get("taskStatus"),
            meta_obj.get("status"),
            meta_obj.get("state"),
            metadata_obj.get("status"),
            metadata_obj.get("state"),
            poll_resp.headers.get("x-task-status"),
            poll_resp.headers.get("x-status"),
        ]

        first_status = ""
        for raw in candidates:
            status = self._normalize_task_status(raw)
            if status:
                first_status = status
                break

        has_error = False
        for obj in (latest, task_obj, result_obj, meta_obj, metadata_obj):
            if self._has_error_signal(obj):
                has_error = True
                break
        if has_error and (not first_status or self._is_in_progress_status(first_status)):
            return "FAILED"
        return first_status

    def _extract_progress_percent(self, latest: dict, poll_resp) -> Optional[float]:
        if not isinstance(latest, dict):
            latest = {}

        task_obj = latest.get("task") if isinstance(latest.get("task"), dict) else {}
        result_obj = (
            latest.get("result") if isinstance(latest.get("result"), dict) else {}
        )
        meta_obj = latest.get("meta") if isinstance(latest.get("meta"), dict) else {}
        metadata_obj = (
            latest.get("metadata") if isinstance(latest.get("metadata"), dict) else {}
        )

        candidates: list[Any] = [
            latest.get("progress"),
            latest.get("percentage"),
            latest.get("percent"),
            latest.get("task_progress"),
            latest.get("taskProgress"),
            task_obj.get("progress"),
            task_obj.get("percentage"),
            result_obj.get("progress"),
            result_obj.get("percentage"),
            meta_obj.get("progress"),
            metadata_obj.get("progress"),
            poll_resp.headers.get("x-task-progress"),
            poll_resp.headers.get("x-progress"),
            poll_resp.headers.get("progress"),
        ]

        for raw in candidates:
            parsed = self._coerce_progress_percent(raw)
            if parsed is not None:
                return parsed
        return None

    @staticmethod
    def _normalize_video_poll_url(raw_url: str) -> str:
        if not raw_url:
            return raw_url
        try:
            parsed = urlparse(raw_url)
            host = parsed.netloc
            path_parts = [p for p in parsed.path.split("/") if p]
            if not host or not path_parts:
                return raw_url
            if not host.startswith("firefly-epo"):
                return raw_url
            job_id = path_parts[-1]
            if not job_id:
                return raw_url
            return f"https://bks-epo8522.adobe.io/v2/jobs/result/{job_id}?host={host}/"
        except Exception:
            return raw_url

    @staticmethod
    def _extract_job_id(raw_url: str) -> str:
        try:
            parsed = urlparse(str(raw_url or ""))
            path_parts = [p for p in parsed.path.split("/") if p]
            if path_parts:
                return path_parts[-1]
        except Exception:
            pass
        return ""

    @staticmethod
    def _build_video_prompt_json(
        prompt: str,
        duration: int,
        negative_prompt: str = "",
        timeline_events: Optional[dict] = None,
        audio: Optional[dict] = None,
    ) -> str:
        payload = {
            "id": 1,
            "duration_sec": int(duration),
            "prompt_text": prompt,
        }
        if negative_prompt:
            payload["negative_prompt"] = negative_prompt
        if isinstance(timeline_events, dict) and timeline_events:
            payload["timeline_events"] = timeline_events
        if isinstance(audio, dict) and audio:
            payload["audio"] = audio
        return json.dumps(payload, ensure_ascii=False)

    def _build_video_payload(
        self,
        video_conf: dict,
        prompt: str,
        aspect_ratio: str,
        duration: int,
        source_image_ids: Optional[list[str]] = None,
        negative_prompt: str = "",
        generate_audio: bool = True,
        locale: str = "en-US",
        timeline_events: Optional[dict] = None,
        audio: Optional[dict] = None,
        reference_mode: str = "frame",
        seeds: Optional[list[int]] = None,
    ) -> dict:
        seed_values = self._normalize_video_seeds(seeds)
        engine = str(video_conf.get("engine") or "sora2")
        upstream_model = str(
            video_conf.get("upstream_model") or "openai:firefly:colligo:sora2"
        )
        resolution = str(video_conf.get("resolution") or "720p")
        if engine == "kling":
            has_source_image = bool(source_image_ids)
            resolution_key = resolution.strip().lower()
            model_version = str(
                video_conf.get("upstream_model_version") or "kling_o3_pro_t2v"
            )
            if has_source_image:
                model_version = str(
                    video_conf.get("upstream_i2v_model_version")
                    or model_version.replace("_t2v", "_i2v")
                )
            else:
                model_versions_by_resolution = (
                    video_conf.get("upstream_model_version_by_resolution") or {}
                )
                if isinstance(model_versions_by_resolution, dict):
                    model_version = str(
                        model_versions_by_resolution.get(resolution_key)
                        or model_version
                    )
            payload = {
                "n": 1,
                "modelId": str(video_conf.get("upstream_model_id") or "kling"),
                "modelVersion": model_version,
                "output": {"storeInputs": True},
                "prompt": prompt,
                "size": self._video_size(aspect_ratio, resolution),
                "generateAudio": bool(generate_audio),
                "generationMetadata": {
                    "module": "image2video" if has_source_image else "text2video"
                },
                "duration": int(duration),
                "generationSettings": {"aspectRatio": aspect_ratio},
                "referenceBlobs": [],
            }
            if seed_values:
                payload["seeds"] = seed_values
            if has_source_image:
                payload["referenceBlobs"] = [
                    {
                        "id": str(image_id),
                        "usage": "frame",
                        "order": idx,
                    }
                    for idx, image_id in enumerate(source_image_ids[:2], start=1)
                ]
            return payload

        if engine in {"veo31-fast", "veo31-standard"}:
            model_version = (
                "3.1-fast-generate" if engine == "veo31-fast" else "3.1-generate"
            )
            payload = {
                "n": 1,
                "modelId": "veo",
                "modelVersion": model_version,
                "output": {"storeInputs": True},
                "prompt": prompt,
                "size": self._video_size(aspect_ratio, resolution),
                "generateAudio": bool(generate_audio),
                "referenceBlobs": [],
                "generationMetadata": {"module": "text2video"},
                "modelSpecificPayload": {
                    "parameters": {
                        "durationSeconds": int(duration),
                        "aspectRatio": aspect_ratio,
                        "addWaterMark": False,
                    }
                },
            }
            if seed_values:
                payload["seeds"] = seed_values
            if source_image_ids:
                if engine == "veo31-standard" and str(reference_mode) == "image":
                    for image_id in source_image_ids[:3]:
                        payload["referenceBlobs"].append(
                            {
                                "id": str(image_id),
                                "usage": "asset",
                            }
                        )
                else:
                    for idx, image_id in enumerate(source_image_ids[:2], start=1):
                        payload["referenceBlobs"].append(
                            {
                                "id": str(image_id),
                                "usage": "general",
                                "promptReference": idx,
                            }
                        )
            return payload

        payload = {
            "size": self._video_size(aspect_ratio, resolution),
            "prompt": prompt,
            "duration": int(duration),
            "generateAudio": bool(generate_audio),
            "generationMetadata": {"module": "text2video"},
            "modelId": "sora",
            "modelVersion": "sora-2",
            "output": {"storeInputs": True},
        }
        if seed_values:
            payload["seeds"] = seed_values
        if negative_prompt:
            payload["negativePrompt"] = negative_prompt
        if source_image_ids:
            payload["referenceBlobs"] = []
            for idx, image_id in enumerate(source_image_ids[:2], start=1):
                blob = {
                    "id": str(image_id),
                    "usage": "general",
                    "promptReference": idx,
                }
                payload["referenceBlobs"].append(blob)
        return payload

    @staticmethod
    def _normalize_video_seeds(
        seeds: Optional[list[int]] = None,
    ) -> Optional[list[int]]:
        normalized: list[int] = []
        for seed in seeds or []:
            try:
                value = int(seed)
            except Exception:
                continue
            if 0 <= value <= 999999:
                normalized.append(value)
        if normalized:
            return normalized[:1]
        return None

    @staticmethod
    def _is_video_unsafe_error(exc: Exception) -> bool:
        status_code = getattr(exc, "status_code", None)
        try:
            if int(status_code or 0) != 451:
                return False
        except Exception:
            return False
        text = str(exc or "").lower()
        return "video_unsafe" in text or "appears to be unsafe" in text or "451" in text

    def generate_video(
        self,
        token: str,
        video_conf: dict,
        prompt: str,
        aspect_ratio: str = "9:16",
        duration: int = 12,
        source_image_ids: Optional[list[str]] = None,
        timeout: int = 600,
        negative_prompt: str = "",
        generate_audio: bool = True,
        locale: str = "en-US",
        timeline_events: Optional[dict] = None,
        audio: Optional[dict] = None,
        reference_mode: str = "frame",
        out_path: Optional[Path] = None,
        progress_cb: Optional[Callable[[dict], None]] = None,
        return_upstream_url: bool = False,
        seeds: Optional[list[int]] = None,
    ) -> tuple[Optional[bytes], dict]:
        payload = self._build_video_payload(
            video_conf=video_conf,
            prompt=prompt,
            aspect_ratio=aspect_ratio,
            duration=duration,
            source_image_ids=source_image_ids,
            negative_prompt=negative_prompt,
            generate_audio=generate_audio,
            locale=locale,
            timeline_events=timeline_events,
            audio=audio,
            reference_mode=reference_mode,
            seeds=seeds,
        )
        submit_resp = self._post_json(
            self.video_submit_url,
            headers=self._submit_headers(token, prompt=prompt),
            payload=payload,
            request_name="video submit",
        )

        if submit_resp.status_code in (401, 403):
            self._raise_auth_or_quota(submit_resp)

        if submit_resp.status_code != 200:
            if submit_resp.status_code in (429, 451) or submit_resp.status_code >= 500:
                raise UpstreamTemporaryError(
                    f"video submit failed: {submit_resp.status_code} {submit_resp.text[:300]}",
                    status_code=submit_resp.status_code,
                    error_type="status",
                )
            raise AdobeRequestError(
                f"video submit failed: {submit_resp.status_code} {submit_resp.text[:300]}"
            )

        submit_data = submit_resp.json()
        poll_url = submit_resp.headers.get("x-override-status-link") or (
            (submit_data.get("links") or {}).get("result") or {}
        ).get("href")
        if not poll_url:
            raise AdobeRequestError("video submit succeeded but no poll url returned")
        poll_url = self._normalize_video_poll_url(str(poll_url))
        upstream_job_id = self._extract_job_id(poll_url)
        if progress_cb:
            try:
                progress_cb(
                    {
                        "task_status": "IN_PROGRESS",
                        "task_progress": 0.0,
                        "upstream_job_id": upstream_job_id,
                        "retry_after": int(submit_resp.headers.get("retry-after") or 0)
                        or None,
                    }
                )
            except Exception:
                pass

        start = time.time()
        last_progress = 0.0
        poll_retry_attempt = 0
        while True:
            try:
                poll_resp = self._get(
                    poll_url,
                    headers=self._poll_headers(token),
                    timeout=60,
                    request_name="video poll",
                )
                if poll_resp.status_code in (401, 403):
                    self._raise_auth_or_quota(poll_resp)
                if poll_resp.status_code != 200:
                    if poll_resp.status_code in (429, 451) or poll_resp.status_code >= 500:
                        raise UpstreamTemporaryError(
                            f"video poll failed: {poll_resp.status_code} {poll_resp.text[:300]}",
                            status_code=poll_resp.status_code,
                            error_type="status",
                        )
                    raise AdobeRequestError(
                        f"video poll failed: {poll_resp.status_code} {poll_resp.text[:300]}"
                    )

                latest = poll_resp.json()
                status_val = self._extract_task_status(latest, poll_resp)
                progress_val = self._extract_progress_percent(latest, poll_resp)
                if progress_val is not None:
                    last_progress = progress_val
                poll_retry_attempt = 0

                if progress_cb and self._is_in_progress_status(status_val):
                    try:
                        progress_cb(
                            {
                                "task_status": "IN_PROGRESS",
                                "task_progress": progress_val
                                if progress_val is not None
                                else 0.0,
                                "upstream_job_id": upstream_job_id,
                                "retry_after": int(
                                    poll_resp.headers.get("retry-after") or 0
                                )
                                or None,
                            }
                        )
                    except Exception:
                        pass

                outputs = latest.get("outputs") or []
                if outputs:
                    video_url = ((outputs[0] or {}).get("video") or {}).get("presignedUrl")
                    if not video_url:
                        raise AdobeRequestError("video job finished without video url")
                    if return_upstream_url:
                        video_bytes = None
                    elif out_path is not None:
                        self._download_to_file(
                            video_url,
                            headers={"accept": "*/*"},
                            out_path=out_path,
                            timeout=60,
                        )
                        video_bytes = None
                    else:
                        video_resp = self._get(
                            video_url,
                            headers={"accept": "*/*"},
                            timeout=60,
                            proxy_kind="resource",
                            request_name="video download",
                        )
                        video_resp.raise_for_status()
                        video_bytes = video_resp.content
                    if progress_cb:
                        try:
                            progress_cb(
                                {
                                    "task_status": "COMPLETED",
                                    "task_progress": 100.0,
                                    "upstream_job_id": upstream_job_id,
                                    "retry_after": None,
                                }
                            )
                        except Exception:
                            pass
                    return video_bytes, latest

                if self._is_failed_status(status_val):
                    if progress_cb:
                        try:
                            progress_cb(
                                {
                                    "task_status": "FAILED",
                                    "task_progress": progress_val
                                    if progress_val is not None
                                    else 0.0,
                                    "upstream_job_id": upstream_job_id,
                                    "retry_after": None,
                                    "error": f"video job failed: {latest}",
                                }
                            )
                        except Exception:
                            pass
                    raise AdobeRequestError(f"video job failed: {latest}")
            except UpstreamTemporaryError as exc:
                if self._is_video_unsafe_error(exc):
                    logger.warning(
                        "video poll returned unsafe result; retrying requires a new upstream job id=%s error=%s",
                        upstream_job_id,
                        str(exc),
                    )
                    raise
                can_retry_same_job = self.should_retry_temporary_error(exc) and (
                    time.time() - start < timeout
                )
                if not can_retry_same_job:
                    raise
                poll_retry_attempt += 1
                retry_delay = max(1.0, self._retry_delay_for_attempt(poll_retry_attempt))
                logger.warning(
                    "video poll temporary error; retrying same upstream job id=%s attempt=%s delay=%.2fs error=%s",
                    upstream_job_id,
                    poll_retry_attempt,
                    retry_delay,
                    str(exc),
                )
                if progress_cb:
                    try:
                        progress_cb(
                            {
                                "task_status": "IN_PROGRESS",
                                "task_progress": last_progress,
                                "upstream_job_id": upstream_job_id,
                                "retry_after": int(retry_delay),
                                "error": f"poll retry {poll_retry_attempt}: {str(exc)[:160]}",
                            }
                        )
                    except Exception:
                        pass
                time.sleep(retry_delay)
                continue

            if time.time() - start > timeout:
                if progress_cb:
                    try:
                        progress_cb(
                            {
                                "task_status": "FAILED",
                                "task_progress": last_progress,
                                "upstream_job_id": upstream_job_id,
                                "retry_after": None,
                                "error": "video generation timed out",
                            }
                        )
                    except Exception:
                        pass
                raise AdobeRequestError("video generation timed out")
            time.sleep(3.0)

    def generate(
        self,
        token: str,
        prompt: str,
        aspect_ratio: str = "16:9",
        output_resolution: str = "2K",
        upstream_model_id: str = "gemini-flash",
        upstream_model_version: str = "nano-banana-2",
        source_image_ids: Optional[list[str]] = None,
        payload_style: str = "banana",
        generation_metadata: Optional[dict] = None,
        generation_settings: Optional[dict] = None,
        model_specific_payload: Optional[dict] = None,
        timeout: int = 180,
        out_path: Optional[Path] = None,
        progress_cb: Optional[Callable[[dict], None]] = None,
        return_upstream_url: bool = False,
        seeds: Optional[list[int]] = None,
    ) -> tuple[Optional[bytes], dict]:
        submit_resp = None
        last_error = ""
        for payload in self._build_payload_candidates(
            prompt=prompt,
            aspect_ratio=aspect_ratio,
            output_resolution=output_resolution,
            upstream_model_id=upstream_model_id,
            upstream_model_version=upstream_model_version,
            source_image_ids=source_image_ids,
            payload_style=payload_style,
            generation_metadata=generation_metadata,
            generation_settings=generation_settings,
            model_specific_payload=model_specific_payload,
            seeds=seeds,
        ):
            submit_resp = self._post_json(
                self.submit_url,
                headers=self._submit_headers(
                    token, prompt=str(payload.get("prompt") or prompt or "")
                ),
                payload=payload,
                request_name="image submit",
            )
            if submit_resp.status_code == 200:
                break

            if submit_resp.status_code in (401, 403):
                break

            last_error = submit_resp.text[:300]

        if submit_resp is None:
            raise AdobeRequestError("submit failed: no response")

        if submit_resp.status_code in (401, 403):
            access_error = submit_resp.headers.get("x-access-error")
            logger.warning(
                "submit auth failed status=%s access_error=%s body=%s",
                submit_resp.status_code,
                access_error,
                submit_resp.text[:300],
            )
            self._raise_auth_or_quota(submit_resp)

        if submit_resp.status_code != 200:
            logger.error(
                "submit failed status=%s body=%s",
                submit_resp.status_code,
                submit_resp.text[:500],
            )
            if submit_resp.status_code in (429, 451) or submit_resp.status_code >= 500:
                raise UpstreamTemporaryError(
                    f"submit failed: {submit_resp.status_code} {submit_resp.text[:300]}",
                    status_code=submit_resp.status_code,
                    error_type="status",
                )
            if last_error:
                raise AdobeRequestError(
                    f"submit failed: {submit_resp.status_code} {last_error}"
                )
            raise AdobeRequestError(
                f"submit failed: {submit_resp.status_code} {submit_resp.text[:300]}"
            )

        submit_data = submit_resp.json()
        poll_url = submit_resp.headers.get("x-override-status-link") or (
            (submit_data.get("links") or {}).get("result") or {}
        ).get("href")
        if not poll_url:
            raise AdobeRequestError("submit succeeded but no poll url returned")

        upstream_job_id = self._extract_job_id(poll_url)
        if progress_cb:
            try:
                progress_cb(
                    {
                        "task_status": "IN_PROGRESS",
                        "task_progress": 0.0,
                        "upstream_job_id": upstream_job_id,
                        "retry_after": int(submit_resp.headers.get("retry-after") or 0)
                        or None,
                    }
                )
            except Exception:
                pass

        start = time.time()
        latest = {}
        sleep_time = 3.0
        last_progress = 0.0
        poll_retry_attempt = 0
        while True:
            try:
                poll_resp = self._get(
                    poll_url,
                    headers=self._poll_headers(token),
                    timeout=60,
                    request_name="image poll",
                )
                if poll_resp.status_code in (401, 403):
                    self._raise_auth_or_quota(poll_resp)
                if poll_resp.status_code != 200:
                    logger.error(
                        "poll failed status=%s body=%s",
                        poll_resp.status_code,
                        poll_resp.text[:500],
                    )
                    if poll_resp.status_code in (429, 451) or poll_resp.status_code >= 500:
                        raise UpstreamTemporaryError(
                            f"poll failed: {poll_resp.status_code} {poll_resp.text[:300]}",
                            status_code=poll_resp.status_code,
                            error_type="status",
                        )
                    raise AdobeRequestError(
                        f"poll failed: {poll_resp.status_code} {poll_resp.text[:300]}"
                    )

                latest = poll_resp.json()
                status_val = self._extract_task_status(latest, poll_resp)
                progress_val = self._extract_progress_percent(latest, poll_resp)
                if progress_val is not None:
                    last_progress = progress_val
                poll_retry_attempt = 0

                if progress_cb and self._is_in_progress_status(status_val):
                    try:
                        progress_cb(
                            {
                                "task_status": "IN_PROGRESS",
                                "task_progress": progress_val
                                if progress_val is not None
                                else 0.0,
                                "upstream_job_id": upstream_job_id,
                                "retry_after": int(
                                    poll_resp.headers.get("retry-after") or 0
                                )
                                or None,
                            }
                        )
                    except Exception:
                        pass

                outputs = latest.get("outputs") or []
                if outputs:
                    image_url = ((outputs[0] or {}).get("image") or {}).get("presignedUrl")
                    if not image_url:
                        raise AdobeRequestError("job finished without image url")
                    if return_upstream_url:
                        image_bytes = None
                    elif out_path is not None:
                        self._download_to_file(
                            image_url,
                            headers={"accept": "*/*"},
                            out_path=out_path,
                            timeout=30,
                        )
                        image_bytes = None
                    else:
                        img_resp = self._get(
                            image_url,
                            headers={"accept": "*/*"},
                            timeout=30,
                            proxy_kind="resource",
                            request_name="image download",
                        )
                        img_resp.raise_for_status()
                        image_bytes = img_resp.content
                    if progress_cb:
                        try:
                            progress_cb(
                                {
                                    "task_status": "COMPLETED",
                                    "task_progress": 100.0,
                                    "upstream_job_id": upstream_job_id,
                                    "retry_after": None,
                                }
                            )
                        except Exception:
                            pass
                    return image_bytes, latest

                if self._is_failed_status(status_val):
                    if progress_cb:
                        try:
                            progress_cb(
                                {
                                    "task_status": "FAILED",
                                    "task_progress": progress_val
                                    if progress_val is not None
                                    else 0.0,
                                    "upstream_job_id": upstream_job_id,
                                    "retry_after": None,
                                    "error": f"image job failed: {latest}",
                                }
                            )
                        except Exception:
                            pass
                    raise AdobeRequestError(f"image job failed: {latest}")
            except UpstreamTemporaryError as exc:
                can_retry_same_job = self.should_retry_temporary_error(exc) and (
                    time.time() - start < timeout
                )
                if not can_retry_same_job:
                    raise
                poll_retry_attempt += 1
                retry_delay = max(1.0, self._retry_delay_for_attempt(poll_retry_attempt))
                logger.warning(
                    "image poll temporary error; retrying same upstream job id=%s attempt=%s delay=%.2fs error=%s",
                    upstream_job_id,
                    poll_retry_attempt,
                    retry_delay,
                    str(exc),
                )
                if progress_cb:
                    try:
                        progress_cb(
                            {
                                "task_status": "IN_PROGRESS",
                                "task_progress": last_progress,
                                "upstream_job_id": upstream_job_id,
                                "retry_after": int(retry_delay),
                                "error": f"poll retry {poll_retry_attempt}: {str(exc)[:160]}",
                            }
                        )
                    except Exception:
                        pass
                time.sleep(retry_delay)
                continue

            if time.time() - start > timeout:
                if progress_cb:
                    try:
                        progress_cb(
                            {
                                "task_status": "FAILED",
                                "task_progress": last_progress,
                                "upstream_job_id": upstream_job_id,
                                "retry_after": None,
                                "error": "image generation timed out",
                            }
                        )
                    except Exception:
                        pass
                raise AdobeRequestError("generation timed out")
            time.sleep(sleep_time)
