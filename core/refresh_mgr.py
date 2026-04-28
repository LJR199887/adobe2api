import json
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import requests

from core.config_mgr import config_manager
from core.proxy_utils import build_requests_proxies, resolve_basic_proxy
from core.sqlite_store import SQLiteStore
from core.token_mgr import token_manager


BASE_DIR = Path(__file__).parent.parent
CONFIG_DIR = BASE_DIR / "config"
PROFILE_FILE = CONFIG_DIR / "refresh_profile.json"


class RefreshManager:
    DEFAULT_REFRESH_URL = "https://adobeid-na1.services.adobe.com/ims/check/v6/token?jslVersion=v2-v0.48.0-1-g1e322cb"
    DEFAULT_SCOPE = (
        "AdobeID,firefly_api,openid,pps.read,pps.write,additional_info.projectedProductContext,"
        "additional_info.ownerOrg,uds_read,uds_write,ab.manage,read_organizations,"
        "additional_info.roles,account_cluster.read,creative_production"
    )

    def __init__(self):
        self._lock = threading.Lock()
        self._runner_started = False
        self._stop_event = threading.Event()
        self._profiles: List[Dict] = []
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        self._store = SQLiteStore(CONFIG_DIR / "app.db")
        self._load_profiles()

    def _load_json_profiles_locked(self) -> List[Dict]:
        if not PROFILE_FILE.exists():
            return []
        try:
            payload = json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []

        profiles = payload.get("profiles") if isinstance(payload, dict) else None
        if not isinstance(profiles, list):
            return []
        return [item for item in profiles if isinstance(item, dict)]

    def _normalize_loaded_profiles(self, profiles: List[Dict]) -> List[Dict]:
        loaded: List[Dict] = []
        now_ts = int(time.time())
        for item in profiles:
            try:
                normalized = self._normalize_stored_profile(item, now_ts)
            except Exception:
                continue
            loaded.append(normalized)
        return loaded

    def _load_profiles(self):
        with self._lock:
            try:
                profiles = self._store.load_refresh_profiles()
            except Exception:
                profiles = []

            if not profiles:
                profiles = self._load_json_profiles_locked()

            self._profiles = self._normalize_loaded_profiles(profiles)
            if self._profiles:
                try:
                    self._store.replace_refresh_profiles(self._profiles)
                except Exception:
                    pass

    def _save_profiles(self):
        payload = {
            "version": 2,
            "profiles": self._profiles,
        }
        try:
            self._store.replace_refresh_profiles(self._profiles)
            return
        except Exception:
            pass
        PROFILE_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    @staticmethod
    def _validate_bundle(bundle: Dict) -> Dict:
        if not isinstance(bundle, dict):
            raise ValueError("bundle must be an object")

        endpoint = bundle.get("endpoint")
        if not isinstance(endpoint, dict):
            raise ValueError("bundle.endpoint is required")

        url = str(endpoint.get("url") or "").strip()
        if not url.startswith(
            "https://adobeid-na1.services.adobe.com/ims/check/v6/token"
        ):
            raise ValueError("invalid endpoint url")

        form = endpoint.get("form")
        headers = endpoint.get("headers")
        if not isinstance(form, dict):
            raise ValueError("bundle.endpoint.form is required")
        if not isinstance(headers, dict):
            raise ValueError("bundle.endpoint.headers is required")

        for key in ("client_id", "scope"):
            if not str(form.get(key) or "").strip():
                raise ValueError(f"bundle form missing {key}")
        if not str(headers.get("Cookie") or "").strip():
            raise ValueError("bundle headers missing Cookie")

        normalized_headers = {
            "Accept": str(headers.get("Accept") or "*/*"),
            "Accept-Language": str(headers.get("Accept-Language") or "en-US,en;q=0.9"),
            "Content-Type": str(
                headers.get("Content-Type")
                or "application/x-www-form-urlencoded;charset=UTF-8"
            ),
            "Cookie": str(headers.get("Cookie") or "").strip(),
            "Origin": str(headers.get("Origin") or "https://firefly.adobe.com"),
            "Referer": str(headers.get("Referer") or "https://firefly.adobe.com/"),
            "User-Agent": str(headers.get("User-Agent") or "Mozilla/5.0"),
        }

        normalized_form = {
            "client_id": str(form.get("client_id") or "").strip(),
            "guest_allowed": str(form.get("guest_allowed") or "true").strip() or "true",
            "scope": str(form.get("scope") or "").strip(),
        }

        return {
            "endpoint": {
                "url": url,
                "method": "POST",
                "form": normalized_form,
                "headers": normalized_headers,
            }
        }

    @classmethod
    def _normalize_stored_profile(cls, profile: Dict, now_ts: int) -> Dict:
        if not isinstance(profile, dict):
            raise ValueError("invalid profile")
        endpoint = profile.get("endpoint")
        validated = cls._validate_bundle({"endpoint": endpoint})
        profile_id = str(profile.get("id") or "").strip() or uuid.uuid4().hex[:8]
        profile_name = str(profile.get("name") or "").strip()
        if not profile_name:
            profile_name = (
                f"{validated['endpoint']['form']['client_id']}-{profile_id[:4]}"
            )

        state = profile.get("state") if isinstance(profile.get("state"), dict) else {}
        account_raw = profile.get("account")
        account = account_raw if isinstance(account_raw, dict) else {}
        return {
            "id": profile_id,
            "name": profile_name,
            "enabled": bool(profile.get("enabled", True)),
            "imported_at": int(profile.get("imported_at") or now_ts),
            "endpoint": validated["endpoint"],
            "account": {
                "display_name": str(account.get("display_name") or "").strip(),
                "email": str(account.get("email") or "").strip(),
                "user_id": str(account.get("user_id") or "").strip(),
                "source": str(account.get("source") or "").strip(),
                "updated_at": account.get("updated_at"),
            },
            "state": {
                "last_attempt_at": state.get("last_attempt_at"),
                "last_success_at": state.get("last_success_at"),
                "last_error": str(state.get("last_error") or ""),
                "last_http_status": state.get("last_http_status"),
                "next_retry_at": state.get("next_retry_at"),
                "consecutive_failures": int(state.get("consecutive_failures") or 0),
            },
        }

    @staticmethod
    def _format_ts(ts_value) -> str:
        if ts_value is None:
            return "-"
        try:
            dt = datetime.fromtimestamp(float(ts_value))
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return "-"

    @staticmethod
    def _refresh_interval_hours() -> int:
        raw = config_manager.get("refresh_interval_hours", 15)
        try:
            hours = int(str(raw or "").strip())
        except Exception:
            return 15
        if hours < 1 or hours > 24:
            return 15
        return hours

    @classmethod
    def _refresh_interval_seconds(cls) -> int:
        return cls._refresh_interval_hours() * 3600

    def _requests_proxies(self):
        return build_requests_proxies(resolve_basic_proxy(config_manager.get_all()))

    def _summary_locked(self, profile: Dict) -> Dict:
        endpoint = profile.get("endpoint", {})
        form = endpoint.get("form", {})
        state = profile.get("state", {})
        account = (
            profile.get("account") if isinstance(profile.get("account"), dict) else {}
        )
        return {
            "id": profile.get("id"),
            "name": profile.get("name"),
            "enabled": bool(profile.get("enabled", True)),
            "imported_at": profile.get("imported_at"),
            "endpoint": {
                "url": endpoint.get("url", ""),
                "client_id": form.get("client_id", ""),
            },
            "account": {
                "display_name": str(account.get("display_name") or "").strip(),
                "email": str(account.get("email") or "").strip(),
                "user_id": str(account.get("user_id") or "").strip(),
                "updated_at": account.get("updated_at"),
            },
            "state": {
                **state,
                "next_refresh_at_text": self._format_ts(state.get("next_retry_at")),
                "last_success_at_text": self._format_ts(state.get("last_success_at")),
                "last_attempt_at_text": self._format_ts(state.get("last_attempt_at")),
            },
            "refresh_interval_hours": self._refresh_interval_hours(),
        }

    def list_profiles(self) -> List[Dict]:
        with self._lock:
            items = [self._summary_locked(p) for p in self._profiles]
        items.sort(key=lambda x: int(x.get("imported_at") or 0), reverse=True)
        return items

    def storage_info(self) -> Dict:
        db_path = self._store.db_path
        with self._lock:
            return {
                "backend": "sqlite",
                "db_path": str(db_path),
                "db_exists": db_path.exists(),
                "db_size_bytes": db_path.stat().st_size if db_path.exists() else 0,
                "refresh_profiles": len(
                    [p for p in self._profiles if isinstance(p, dict)]
                ),
            }

    @staticmethod
    def _cookie_string_from_input(cookie_input) -> str:
        if isinstance(cookie_input, str):
            text = cookie_input.strip()
            if text.lower().startswith("cookie:"):
                text = text.split(":", 1)[1].strip()
            return text

        if isinstance(cookie_input, dict):
            if isinstance(cookie_input.get("cookies"), list):
                cookie_input = cookie_input.get("cookies")
            elif isinstance(cookie_input.get("cookie"), (str, list, dict)):
                cookie_input = cookie_input.get("cookie")
            else:
                return ""

        if isinstance(cookie_input, list):
            pairs: List[str] = []
            for item in cookie_input:
                if isinstance(item, str):
                    txt = item.strip()
                    if txt:
                        pairs.append(txt)
                    continue
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                value = str(item.get("value") or "").strip()
                if not name:
                    continue
                pairs.append(f"{name}={value}")
            return "; ".join(pairs)
        return ""

    @classmethod
    def cookie_fingerprint(cls, cookie_input) -> str:
        cookie = cls._cookie_string_from_input(cookie_input)
        if not cookie:
            return ""

        pairs: List[List[str]] = []
        for part in cookie.split(";"):
            text = part.strip()
            if not text:
                continue
            if "=" in text:
                key, val = text.split("=", 1)
                key = key.strip()
                val = val.strip()
            else:
                key = text.strip()
                val = ""
            if key:
                pairs.append([key, val])

        if not pairs:
            return ""
        pairs.sort(key=lambda item: (item[0].casefold(), item[0], item[1]))
        return json.dumps(pairs, ensure_ascii=False, separators=(",", ":"))

    def import_cookie(self, cookie_input, name: Optional[str] = None) -> Dict:
        cookie = self._cookie_string_from_input(cookie_input)
        if not cookie:
            raise ValueError("cookie is required")
        cookie_fingerprint = self.cookie_fingerprint(cookie)
        validated = self._validate_bundle(
            {
                "endpoint": {
                    "url": self.DEFAULT_REFRESH_URL,
                    "method": "POST",
                    "form": {
                        "client_id": "clio-playground-web",
                        "guest_allowed": "true",
                        "scope": self.DEFAULT_SCOPE,
                    },
                    "headers": {
                        "Accept": "*/*",
                        "Accept-Language": "zh-CN,zh;q=0.9",
                        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                        "Cookie": cookie,
                        "Origin": "https://firefly.adobe.com",
                        "Referer": "https://firefly.adobe.com/",
                        "User-Agent": "Mozilla/5.0",
                    },
                }
            }
        )

        now_ts = int(time.time())
        profile_id = uuid.uuid4().hex[:8]
        profile_name = str(name or "").strip()
        if not profile_name:
            profile_name = (
                f"{validated['endpoint']['form']['client_id']}-{profile_id[:4]}"
            )

        new_profile = {
            "id": profile_id,
            "name": profile_name,
            "enabled": True,
            "imported_at": now_ts,
            "endpoint": validated["endpoint"],
            "account": {
                "display_name": "",
                "email": "",
                "user_id": "",
                "source": "",
                "updated_at": None,
            },
            "state": {
                "last_attempt_at": None,
                "last_success_at": None,
                "last_error": "",
                "last_http_status": None,
                "next_retry_at": time.time() + self._refresh_interval_seconds(),
                "consecutive_failures": 0,
            },
        }

        removed_profile_ids: List[str] = []
        with self._lock:
            target = None
            retained_profiles: List[Dict] = []
            for profile in self._profiles:
                endpoint = (
                    profile.get("endpoint")
                    if isinstance(profile.get("endpoint"), dict)
                    else {}
                )
                headers = (
                    endpoint.get("headers")
                    if isinstance(endpoint.get("headers"), dict)
                    else {}
                )
                existing_cookie = str(headers.get("Cookie") or "").strip()
                is_same_cookie = (
                    bool(cookie_fingerprint)
                    and self.cookie_fingerprint(existing_cookie) == cookie_fingerprint
                )
                if not is_same_cookie:
                    retained_profiles.append(profile)
                    continue

                if target is None:
                    state = (
                        profile.get("state")
                        if isinstance(profile.get("state"), dict)
                        else {}
                    )
                    account = (
                        profile.get("account")
                        if isinstance(profile.get("account"), dict)
                        else {}
                    )
                    profile["enabled"] = True
                    profile["imported_at"] = now_ts
                    profile["endpoint"] = validated["endpoint"]
                    if str(name or "").strip():
                        profile["name"] = profile_name
                    state["last_error"] = ""
                    state["consecutive_failures"] = 0
                    state["next_retry_at"] = time.time() + self._refresh_interval_seconds()
                    profile["state"] = state
                    profile["account"] = account
                    target = profile
                    retained_profiles.append(profile)
                    continue

                removed_profile_ids.append(str(profile.get("id") or "").strip())

            self._profiles = retained_profiles
            if target is None:
                target = new_profile
                self._profiles.append(target)
            self._save_profiles()

        reused_existing_profile = str(target.get("id") or "").strip() != profile_id
        for profile_id in removed_profile_ids:
            if profile_id:
                token_manager.remove_auto_refresh_by_profile(profile_id)

        with self._lock:
            current = self._find_profile_locked(str(target.get("id") or "").strip())
            if not current:
                raise KeyError("profile not found after import")
            summary = self._summary_locked(current)
            summary["reused_existing_profile"] = reused_existing_profile
            return summary

    def export_cookies(self, ids: Optional[List[str]] = None) -> List[Dict]:
        selected_ids = None
        if isinstance(ids, list):
            normalized = [str(x or "").strip() for x in ids]
            selected_ids = {x for x in normalized if x}
        with self._lock:
            out: List[Dict] = []
            for p in self._profiles:
                pid = str(p.get("id") or "").strip()
                if selected_ids is not None and pid not in selected_ids:
                    continue
                endpoint = (
                    p.get("endpoint") if isinstance(p.get("endpoint"), dict) else {}
                )
                headers = (
                    endpoint.get("headers")
                    if isinstance(endpoint.get("headers"), dict)
                    else {}
                )
                cookie = str(headers.get("Cookie") or "").strip()
                out.append(
                    {
                        "id": pid,
                        "name": str(p.get("name") or "").strip(),
                        "cookie": cookie,
                    }
                )
            return out

    def is_profile_enabled(self, profile_id: str) -> Optional[bool]:
        pid = str(profile_id or "").strip()
        if not pid:
            return None
        with self._lock:
            target = self._find_profile_locked(pid)
            if not target:
                return None
            return bool(target.get("enabled", True))

    def profiles_enabled(self, profile_ids: List[str]) -> Dict[str, Optional[bool]]:
        lookup_ids = {str(x or "").strip() for x in profile_ids if str(x or "").strip()}
        if not lookup_ids:
            return {}
        result: Dict[str, Optional[bool]] = {pid: None for pid in lookup_ids}
        with self._lock:
            for profile in self._profiles:
                pid = str(profile.get("id") or "").strip()
                if pid in lookup_ids:
                    result[pid] = bool(profile.get("enabled", True))
        return result

    def _find_profile_locked(self, profile_id: str) -> Optional[Dict]:
        for p in self._profiles:
            if p.get("id") == profile_id:
                return p
        return None

    def remove_profile(self, profile_id: str):
        with self._lock:
            target = self._find_profile_locked(profile_id)
            if not target:
                raise KeyError("profile not found")
            self._profiles = [p for p in self._profiles if p.get("id") != profile_id]
            self._save_profiles()
        token_manager.remove_auto_refresh_by_profile(profile_id)

    def _remove_profiles_only(self, profile_ids: List[str]):
        remove_ids = {str(x or "").strip() for x in profile_ids if str(x or "").strip()}
        if not remove_ids:
            return
        with self._lock:
            before_count = len(self._profiles)
            self._profiles = [
                p
                for p in self._profiles
                if str(p.get("id") or "").strip() not in remove_ids
            ]
            if len(self._profiles) != before_count:
                self._save_profiles()

    def set_enabled(self, profile_id: str, enabled: bool) -> Dict:
        with self._lock:
            target = self._find_profile_locked(profile_id)
            if not target:
                raise KeyError("profile not found")
            enabled_value = bool(enabled)
            old_enabled = bool(target.get("enabled", True))
            target["enabled"] = enabled_value
            state = target.setdefault("state", {})
            if enabled_value:
                state["next_retry_at"] = time.time() + self._refresh_interval_seconds()
                state["last_error"] = ""
                state["consecutive_failures"] = 0
            if old_enabled != enabled_value or enabled_value:
                self._save_profiles()
            return self._summary_locked(target)

    def set_enabled_many(self, profile_ids: List[str], enabled: bool) -> Dict:
        lookup_ids = {str(x or "").strip() for x in profile_ids if str(x or "").strip()}
        if not lookup_ids:
            return {"requested": 0, "matched": 0, "changed": 0}
        enabled_value = bool(enabled)
        matched = 0
        changed = 0
        now_next_retry = time.time() + self._refresh_interval_seconds()
        with self._lock:
            for profile in self._profiles:
                pid = str(profile.get("id") or "").strip()
                if pid not in lookup_ids:
                    continue
                matched += 1
                old_enabled = bool(profile.get("enabled", True))
                if old_enabled != enabled_value:
                    changed += 1
                profile["enabled"] = enabled_value
                if enabled_value:
                    state = profile.setdefault("state", {})
                    state["next_retry_at"] = now_next_retry
                    state["last_error"] = ""
                    state["consecutive_failures"] = 0
            if changed or enabled_value:
                self._save_profiles()
        return {"requested": len(lookup_ids), "matched": matched, "changed": changed}

    def _prepare_refresh(self, profile_id: str) -> Dict:
        with self._lock:
            target = self._find_profile_locked(profile_id)
            if not target:
                raise KeyError("profile not found")
            if not bool(target.get("enabled", True)):
                raise ValueError("profile is disabled")
            endpoint = target.get("endpoint", {})
            state = target.setdefault("state", {})
            state["last_attempt_at"] = int(time.time())
            snapshot = {
                "id": target.get("id"),
                "name": target.get("name"),
                "url": endpoint.get("url"),
                "headers": dict(endpoint.get("headers") or {}),
                "form": dict(endpoint.get("form") or {}),
            }
            self._save_profiles()
            return snapshot

    def _mark_success(self, profile_id: str, http_status: int):
        with self._lock:
            target = self._find_profile_locked(profile_id)
            if not target:
                return
            state = target.setdefault("state", {})
            state["last_http_status"] = int(http_status)
            state["last_success_at"] = int(time.time())
            state["last_error"] = ""
            state["consecutive_failures"] = 0
            state["next_retry_at"] = time.time() + self._refresh_interval_seconds()
            self._save_profiles()

    def _mark_failure(
        self, profile_id: str, message: str, http_status: Optional[int] = None
    ):
        with self._lock:
            target = self._find_profile_locked(profile_id)
            if not target:
                return
            state = target.setdefault("state", {})
            fails = int(state.get("consecutive_failures", 0)) + 1
            state["consecutive_failures"] = fails
            state["last_error"] = str(message or "")[:500]
            if http_status is not None:
                state["last_http_status"] = int(http_status)
            delays = [60, 180, 600, 1800]
            delay = delays[min(fails - 1, len(delays) - 1)]
            state["next_retry_at"] = time.time() + delay
            self._save_profiles()

    def _fetch_account_info(self, access_token: str) -> Dict:
        token = str(access_token or "").strip()
        if not token:
            return {}
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        profile_urls = [
            "https://ims-na1.adobelogin.com/ims/profile/v1",
            "https://adobeid-na1.services.adobe.com/ims/profile/v1",
        ]
        for url in profile_urls:
            try:
                resp = requests.get(
                    url,
                    headers=headers,
                    timeout=15,
                    proxies=self._requests_proxies(),
                )
            except Exception:
                continue
            if resp.status_code != 200:
                continue
            try:
                data = resp.json()
            except Exception:
                continue
            if not isinstance(data, dict):
                continue

            display_name = str(
                data.get("displayName")
                or data.get("name")
                or data.get("fullName")
                or ""
            ).strip()
            email = str(data.get("email") or "").strip()
            user_id = str(data.get("userId") or data.get("authId") or "").strip()
            if not (display_name or email or user_id):
                continue
            return {
                "display_name": display_name,
                "email": email,
                "user_id": user_id,
                "source": "ims_profile_v1",
                "updated_at": int(time.time()),
            }
        return {}

    @staticmethod
    def _extract_account_id(access_token: str) -> str:
        try:
            payload = token_manager._decode_jwt_payload(access_token)  # type: ignore[attr-defined]
        except Exception:
            payload = None
        if not isinstance(payload, dict):
            return ""
        return str(
            payload.get("user_id") or payload.get("aa_id") or payload.get("sub") or ""
        ).strip()

    def _fetch_credits_balance(self, access_token: str, account_id: str) -> Dict:
        token = str(access_token or "").strip()
        aid = str(account_id or "").strip()
        if not token:
            raise RuntimeError("empty access token")
        if not aid:
            raise RuntimeError("missing account id")

        resp = requests.get(
            "https://firefly.adobe.io/v1/credits/balance",
            headers={
                "Authorization": f"Bearer {token}",
                "x-api-key": "SunbreakWebUI1",
                "x-account-id": aid,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=20,
            proxies=self._requests_proxies(),
        )
        if resp.status_code != 200:
            raise RuntimeError(f"credits request failed: {resp.status_code}")
        try:
            payload = resp.json()
        except Exception:
            raise RuntimeError("credits response invalid json")
        total_info = payload.get("total", {}) if isinstance(payload, dict) else {}
        quota = total_info.get("quota", {}) if isinstance(total_info, dict) else {}
        return {
            "total": quota.get("total"),
            "used": quota.get("used"),
            "available": quota.get("available"),
            "available_until": total_info.get("availableUntil"),
            "updated_at": int(time.time()),
        }

    def refresh_credits_for_token_id(self, token_id: str) -> Dict:
        token_info = token_manager.get_by_id(token_id)
        if not token_info:
            raise KeyError("token not found")
        token_value = str(token_info.get("value") or "").strip()
        account_id = self._extract_account_id(token_value)
        credits = self._fetch_credits_balance(token_value, account_id)
        token_manager.set_credits(token_id, credits)
        return {
            "token_id": token_id,
            "credits": credits,
        }

    def _set_profile_account(self, profile_id: str, account: Dict):
        if not account:
            return
        with self._lock:
            target = self._find_profile_locked(profile_id)
            if not target:
                return
            current = (
                target.get("account") if isinstance(target.get("account"), dict) else {}
            )
            merged = {
                "display_name": str(
                    account.get("display_name") or current.get("display_name") or ""
                ).strip(),
                "email": str(
                    account.get("email") or current.get("email") or ""
                ).strip(),
                "user_id": str(
                    account.get("user_id") or current.get("user_id") or ""
                ).strip(),
                "source": str(
                    account.get("source") or current.get("source") or ""
                ).strip(),
                "updated_at": account.get("updated_at") or current.get("updated_at"),
            }
            target["account"] = merged
            display_name = merged.get("display_name")
            email = merged.get("email")
            if display_name or email:
                target["name"] = display_name or email
            self._save_profiles()

    def refresh_once(self, profile_id: str, refresh_credits: bool = True) -> Dict:
        total_started = time.perf_counter()
        prepare_started = time.perf_counter()
        snapshot = self._prepare_refresh(profile_id)
        prepare_ms = round((time.perf_counter() - prepare_started) * 1000, 3)

        request_started = time.perf_counter()
        resp = requests.post(
            snapshot["url"],
            headers=snapshot["headers"],
            data=snapshot["form"],
            timeout=30,
            proxies=self._requests_proxies(),
        )
        adobe_refresh_ms = round((time.perf_counter() - request_started) * 1000, 3)

        if resp.status_code != 200:
            self._mark_failure(
                profile_id,
                f"refresh request failed: {resp.status_code} {resp.text[:200]}",
                http_status=resp.status_code,
            )
            raise RuntimeError(
                f"refresh request failed: {resp.status_code} {resp.text[:200]}"
            )

        parse_started = time.perf_counter()
        try:
            data = resp.json()
        except Exception:
            self._mark_failure(
                profile_id,
                "refresh response is not valid json",
                http_status=resp.status_code,
            )
            raise RuntimeError("refresh response is not valid json")
        response_parse_ms = round((time.perf_counter() - parse_started) * 1000, 3)

        token = str(data.get("access_token") or "").strip()
        if not token:
            self._mark_failure(
                profile_id,
                "refresh response missing access_token",
                http_status=resp.status_code,
            )
            raise RuntimeError("refresh response missing access_token")

        account_started = time.perf_counter()
        account = self._fetch_account_info(token)
        if account:
            self._set_profile_account(profile_id, account)
        account_ms = round((time.perf_counter() - account_started) * 1000, 3)

        profile_name = str(
            account.get("display_name")
            or account.get("email")
            or snapshot["name"]
            or ""
        ).strip()
        profile_email = str(account.get("email") or "").strip()

        upsert_started = time.perf_counter()
        token_record = token_manager.upsert_auto_refresh_token(
            token,
            profile_id=snapshot["id"],
            profile_name=profile_name,
            profile_email=profile_email,
        )
        token_upsert_ms = round((time.perf_counter() - upsert_started) * 1000, 3)
        merged_profile_ids = [
            str(x or "").strip()
            for x in token_record.get("_merged_refresh_profile_ids", [])
            if str(x or "").strip() and str(x or "").strip() != str(snapshot["id"])
        ]
        self._remove_profiles_only(merged_profile_ids)

        credits_error = ""
        credits_skipped = False
        token_id = str(token_record.get("id") or "").strip()
        credits_started = time.perf_counter()
        if token_id and refresh_credits:
            try:
                self.refresh_credits_for_token_id(token_id)
            except Exception as exc:
                credits_error = str(exc)
                token_manager.set_credits_error(token_id, credits_error)
        elif token_id:
            credits_skipped = True
        credits_ms = round((time.perf_counter() - credits_started) * 1000, 3)

        self._mark_success(profile_id, http_status=resp.status_code)

        token_timing = token_record.get("_timing") or {}
        return {
            "status": "ok",
            "profile_id": snapshot["id"],
            "profile_name": profile_name,
            "profile_email": profile_email,
            "expires_in": data.get("expires_in"),
            "credits_error": credits_error,
            "credits_skipped": credits_skipped,
            "token_duplicate": bool(token_record.get("_duplicate_token")),
            "token_created": bool(token_record.get("_created")),
            "timing": {
                "prepare_ms": prepare_ms,
                "adobe_refresh_ms": adobe_refresh_ms,
                "response_parse_ms": response_parse_ms,
                "account_ms": account_ms,
                "token_upsert_ms": token_upsert_ms,
                "token_upsert_index_ms": token_timing.get("index_lookup_ms", 0),
                "token_upsert_total_ms": token_timing.get("upsert_total_ms", 0),
                "credits_ms": credits_ms,
                "total_ms": round((time.perf_counter() - total_started) * 1000, 3),
                "token_value_index_size": token_timing.get("value_index_size", 0),
                "token_id_index_size": token_timing.get("id_index_size", 0),
                "token_profile_index_size": token_timing.get("profile_index_size", 0),
            },
        }

    def start(self):
        with self._lock:
            if self._runner_started:
                return
            self._runner_started = True
        t = threading.Thread(target=self._run, daemon=True)
        t.start()

    def _run(self):
        while not self._stop_event.is_set():
            try:
                with self._lock:
                    candidates = [
                        {
                            "id": p.get("id"),
                            "enabled": bool(p.get("enabled", True)),
                            "next_retry_at": p.get("state", {}).get("next_retry_at"),
                        }
                        for p in self._profiles
                    ]

                now_ts = time.time()
                for item in candidates:
                    if not item.get("enabled"):
                        continue
                    next_retry = item.get("next_retry_at")
                    if next_retry and now_ts < float(next_retry):
                        continue
                    pid = str(item.get("id") or "")
                    if not pid:
                        continue
                    try:
                        self.refresh_once(pid)
                    except Exception:
                        pass
            except Exception:
                pass
            time.sleep(2.0)


refresh_manager = RefreshManager()
