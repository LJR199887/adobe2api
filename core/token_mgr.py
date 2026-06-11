import json
import base64
import logging
import threading
import time
import uuid
import random
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict

from core.sqlite_store import SQLiteStore

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
CONFIG_DIR = BASE_DIR / "config"
DATA_FILE = CONFIG_DIR / "tokens.json"
LEGACY_DATA_FILE = DATA_DIR / "tokens.json"
logger = logging.getLogger("uvicorn.error")


class TokenManager:
    ERROR_COOLDOWN_SECONDS = 180

    def __init__(self):
        self._lock = threading.Lock()
        self.tokens: List[Dict] = []
        self._id_index: Dict[str, Dict] = {}
        self._value_index: Dict[str, Dict] = {}
        self._auto_refresh_profile_index: Dict[str, Dict] = {}
        self._expired_scanner_started = False
        self._expired_scanner_stop_event = threading.Event()
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        self._store = SQLiteStore(CONFIG_DIR / "app.db")
        self.load()

    def _load_json_tokens_locked(self) -> List[Dict]:
        source = DATA_FILE if DATA_FILE.exists() else LEGACY_DATA_FILE
        if not source.exists():
            return []
        try:
            loaded = json.loads(source.read_text(encoding="utf-8"))
        except Exception:
            return []
        if not isinstance(loaded, list):
            return []
        if source == LEGACY_DATA_FILE and not DATA_FILE.exists():
            try:
                DATA_FILE.write_text(json.dumps(loaded, indent=2), encoding="utf-8")
            except Exception:
                pass
        return [item for item in loaded if isinstance(item, dict)]

    @staticmethod
    def _normalize_loaded_tokens(tokens: List[Dict]) -> List[Dict]:
        normalized: List[Dict] = []
        now_ts = time.time()
        for token in tokens:
            if not isinstance(token, dict):
                continue
            token.setdefault("id", uuid.uuid4().hex[:8])
            token.setdefault("value", "")
            token.setdefault("status", "active")
            token.setdefault("fails", 0)
            token.setdefault("success_count", 0)
            token.setdefault("added_at", now_ts)
            token.setdefault("error_until", 0)
            token.pop("lease_id", None)
            token.pop("leased_at", None)
            token.pop("lease_count", None)
            normalized.append(token)
        return normalized

    def load(self):
        with self._lock:
            try:
                self.tokens = self._store.load_tokens()
            except Exception:
                self.tokens = []

            if not self.tokens:
                self.tokens = self._load_json_tokens_locked()

            self.tokens = self._normalize_loaded_tokens(self.tokens)
            if self.tokens:
                try:
                    self._store.replace_tokens(self.tokens)
                except Exception:
                    pass
            self._rebuild_indexes_locked()

    def save(self):
        try:
            self._store.replace_tokens(self.tokens)
            return
        except Exception:
            pass
        DATA_FILE.write_text(json.dumps(self.tokens, indent=2), encoding="utf-8")

    @staticmethod
    def _normalize_token_value(value: str) -> str:
        token = str(value or "").strip()
        if token.startswith("Bearer "):
            token = token[7:].strip()
        return token

    @staticmethod
    def _token_id_key(token: Dict) -> str:
        return str(token.get("id") or "").strip()

    @classmethod
    def _token_value_key(cls, token: Dict) -> str:
        return cls._normalize_token_value(token.get("value") or "")

    @staticmethod
    def _token_profile_key(token: Dict) -> str:
        if token.get("auto_refresh") is not True:
            return ""
        return str(token.get("refresh_profile_id") or "").strip()

    def _index_token_locked(self, token: Dict):
        tid = self._token_id_key(token)
        if tid:
            self._id_index[tid] = token

        value = self._token_value_key(token)
        if value and value not in self._value_index:
            self._value_index[value] = token

        profile_id = self._token_profile_key(token)
        if profile_id and profile_id not in self._auto_refresh_profile_index:
            self._auto_refresh_profile_index[profile_id] = token

    def _drop_index_keys_locked(
        self,
        token: Dict,
        tid: str = "",
        value: str = "",
        profile_id: str = "",
    ):
        if tid and self._id_index.get(tid) is token:
            self._id_index.pop(tid, None)
        if value and self._value_index.get(value) is token:
            self._value_index.pop(value, None)
        if profile_id and self._auto_refresh_profile_index.get(profile_id) is token:
            self._auto_refresh_profile_index.pop(profile_id, None)

    def _reindex_token_locked(
        self,
        token: Dict,
        old_tid: str = "",
        old_value: str = "",
        old_profile_id: str = "",
    ):
        self._drop_index_keys_locked(
            token,
            tid=old_tid,
            value=old_value,
            profile_id=old_profile_id,
        )
        self._index_token_locked(token)

    def _remove_token_locked(self, token: Dict):
        self._drop_index_keys_locked(
            token,
            tid=self._token_id_key(token),
            value=self._token_value_key(token),
            profile_id=self._token_profile_key(token),
        )
        self.tokens = [item for item in self.tokens if item is not token]

    def _rebuild_indexes_locked(self):
        self._id_index = {}
        self._value_index = {}
        self._auto_refresh_profile_index = {}
        for token in self.tokens:
            if isinstance(token, dict):
                self._index_token_locked(token)

    def has_value(self, value: str) -> bool:
        value = self._normalize_token_value(value)
        if not value:
            return False
        with self._lock:
            return value in self._value_index

    def add(self, value: str, meta: Optional[Dict] = None):
        with self._lock:
            value = self._normalize_token_value(value)
            meta = dict(meta or {})

            existing = self._value_index.get(value)
            if existing is not None:
                if meta:
                    old_tid = self._token_id_key(existing)
                    old_value = self._token_value_key(existing)
                    old_profile_id = self._token_profile_key(existing)
                    existing.update(meta)
                    self._reindex_token_locked(
                        existing,
                        old_tid=old_tid,
                        old_value=old_value,
                        old_profile_id=old_profile_id,
                    )
                    self.save()
                result = dict(existing)
                result["_created"] = False
                result["_duplicate"] = True
                return result

            new_token = {
                "id": uuid.uuid4().hex[:8],
                "value": value,
                "status": "active",
                "fails": 0,
                "success_count": 0,
                "added_at": time.time(),
                "error_until": 0,
            }
            if meta:
                new_token.update(meta)
            self.tokens.append(new_token)
            self._index_token_locked(new_token)
            self.save()
            result = dict(new_token)
            result["_created"] = True
            result["_duplicate"] = False
            return result

    def upsert_auto_refresh_token(
        self,
        value: str,
        profile_id: str,
        profile_name: Optional[str] = None,
        profile_email: Optional[str] = None,
    ):
        total_started = time.perf_counter()
        with self._lock:
            value = self._normalize_token_value(value)

            now_ts = time.time()
            pid = str(profile_id or "").strip()
            if not pid:
                raise ValueError("profile_id is required")

            lookup_started = time.perf_counter()
            value_target = self._value_index.get(value)
            profile_target = self._auto_refresh_profile_index.get(pid)
            index_lookup_ms = round((time.perf_counter() - lookup_started) * 1000, 3)

            def build_timing() -> Dict:
                return {
                    "index_lookup_ms": index_lookup_ms,
                    "upsert_total_ms": round(
                        (time.perf_counter() - total_started) * 1000, 3
                    ),
                    "value_index_size": len(self._value_index),
                    "id_index_size": len(self._id_index),
                    "profile_index_size": len(self._auto_refresh_profile_index),
                }

            # A re-imported cookie may create a new profile but return an access
            # token that already exists. Keep one token row and make the latest
            # refresh profile own it.
            target = value_target or profile_target
            if target is not None:
                duplicate_token = value_target is not None
                removed_profile_ids = set()
                previous_profile_id = str(target.get("refresh_profile_id") or "").strip()
                if previous_profile_id and previous_profile_id != pid:
                    removed_profile_ids.add(previous_profile_id)

                old_tid = self._token_id_key(target)
                old_value = self._token_value_key(target)
                old_profile_id = self._token_profile_key(target)

                target["value"] = value
                target["status"] = "active"
                target["fails"] = 0
                target["error_until"] = 0
                target.setdefault("success_count", 0)
                target["updated_at"] = now_ts
                target["source"] = "auto_refresh"
                target["auto_refresh"] = True
                target["refresh_profile_id"] = pid
                target["refresh_profile_name"] = str(profile_name or "").strip() or pid
                target["refresh_profile_email"] = str(profile_email or "").strip()

                if profile_target is not None and profile_target is not target:
                    profile_target_id = str(
                        profile_target.get("refresh_profile_id") or ""
                    ).strip()
                    if profile_target_id and profile_target_id != pid:
                        removed_profile_ids.add(profile_target_id)
                    self._remove_token_locked(profile_target)

                self._reindex_token_locked(
                    target,
                    old_tid=old_tid,
                    old_value=old_value,
                    old_profile_id=old_profile_id,
                )

                self.save()
                result = dict(target)
                result["_created"] = False
                result["_duplicate_token"] = duplicate_token
                result["_timing"] = build_timing()
                if removed_profile_ids:
                    result["_merged_refresh_profile_ids"] = sorted(removed_profile_ids)
                return result

            new_token = {
                "id": uuid.uuid4().hex[:8],
                "value": value,
                "status": "active",
                "fails": 0,
                "success_count": 0,
                "added_at": now_ts,
                "updated_at": now_ts,
                "error_until": 0,
                "source": "auto_refresh",
                "auto_refresh": True,
                "refresh_profile_id": pid,
                "refresh_profile_name": str(profile_name or "").strip() or pid,
                "refresh_profile_email": str(profile_email or "").strip(),
            }
            self.tokens.append(new_token)
            self._index_token_locked(new_token)
            self.save()
            result = dict(new_token)
            result["_created"] = True
            result["_duplicate_token"] = False
            result["_timing"] = build_timing()
            return result

    def remove(self, tid: str):
        with self._lock:
            target = self._id_index.get(str(tid or "").strip())
            if target is not None:
                self._remove_token_locked(target)
                self.save()

    def remove_many(self, token_ids: List[str]) -> Dict:
        remove_ids = {str(x or "").strip() for x in token_ids if str(x or "").strip()}
        if not remove_ids:
            return {"deleted_ids": [], "missing_ids": []}
        with self._lock:
            deleted_ids = []
            for tid in remove_ids:
                target = self._id_index.get(tid)
                if target is None:
                    continue
                self._remove_token_locked(target)
                deleted_ids.append(tid)
            if deleted_ids:
                self.save()
            missing_ids = sorted(remove_ids - set(deleted_ids))
            return {
                "deleted_ids": sorted(deleted_ids),
                "missing_ids": missing_ids,
            }

    def remove_auto_refresh_by_profile(self, profile_id: str):
        pid = str(profile_id or "").strip()
        if not pid:
            return
        with self._lock:
            target = self._auto_refresh_profile_index.get(pid)
            if target is not None:
                self._remove_token_locked(target)
                self.save()

    def get_by_id(self, tid: str) -> Optional[Dict]:
        with self._lock:
            token = self._id_index.get(str(tid or "").strip())
            if token is not None:
                return dict(token)
        return None

    def get_meta_by_value(self, value: str) -> Dict:
        token_value = self._normalize_token_value(value)
        with self._lock:
            t = self._value_index.get(token_value)
            if t is not None:
                return {
                    "token_id": t.get("id"),
                    "token_account_name": t.get("refresh_profile_name") or "",
                    "token_account_email": t.get("refresh_profile_email") or "",
                    "token_source": t.get("source") or "manual",
                }
        return {
            "token_id": "",
            "token_account_name": "",
            "token_account_email": "",
            "token_source": "manual",
        }

    def set_status(self, tid: str, status: str):
        with self._lock:
            t = self._id_index.get(str(tid or "").strip())
            if t is not None:
                t["status"] = status
                t["fails"] = 0 if status == "active" else t["fails"]
                if status == "active":
                    t["error_until"] = 0
            self.save()

    def set_credits(self, tid: str, credits: Dict):
        with self._lock:
            t = self._id_index.get(str(tid or "").strip())
            if t is not None:
                t["credits_total"] = credits.get("total")
                t["credits_used"] = credits.get("used")
                t["credits_available"] = credits.get("available")
                t["credits_available_until"] = credits.get("available_until")
                t["credits_updated_at"] = credits.get("updated_at") or int(time.time())
                t["credits_error"] = ""
                self.save()
                return dict(t)
        return None

    def set_credits_error(self, tid: str, error_message: str):
        with self._lock:
            t = self._id_index.get(str(tid or "").strip())
            if t is not None:
                t["credits_error"] = str(error_message or "")[:300]
                t["credits_updated_at"] = int(time.time())
                self.save()
                return dict(t)
        return None

    def list_active_ids(self) -> List[str]:
        with self._lock:
            return [
                str(t.get("id") or "")
                for t in self.tokens
                if t.get("status") == "active"
            ]

    @staticmethod
    def _token_lease_count(token: Dict) -> int:
        try:
            count = int(token.get("lease_count", 0) or 0)
        except Exception:
            count = 0
        if count <= 0 and str(token.get("lease_id") or "").strip():
            return 1
        return max(0, count)

    @classmethod
    def _token_has_capacity(cls, token: Dict, concurrency_limit: int) -> bool:
        return cls._token_lease_count(token) < concurrency_limit

    @classmethod
    def _acquire_token_lease(cls, token: Dict) -> None:
        token["lease_count"] = cls._token_lease_count(token) + 1
        token["leased_at"] = time.time()
        token.pop("lease_id", None)

    def _pick_active_token_locked(
        self, strategy: str = "round_robin", concurrency_limit: int = 1
    ) -> Optional[Dict]:
        concurrency_limit = max(1, min(int(concurrency_limit or 1), 10))
        mode = str(strategy or "round_robin").strip().lower()
        if mode not in {"finish_success", "random"}:
            for token in self.tokens:
                if (
                    token["status"] == "active"
                    and self._token_has_capacity(token, concurrency_limit)
                ):
                    return token
            return None

        active = [
            t
            for t in self.tokens
            if t["status"] == "active"
            and self._token_has_capacity(t, concurrency_limit)
        ]
        if not active:
            return None

        chosen = None
        if mode == "finish_success":
            active.sort(
                key=lambda t: (
                    -max(0, int(t.get("success_count", 0) or 0)),
                    self._token_sort_ts(t),
                    str(t.get("id") or ""),
                )
            )
            chosen = active[0]
        else:
            chosen = random.choice(active)
        return chosen

    def get_available(
        self, strategy: str = "round_robin", concurrency_limit: int = 1
    ) -> Optional[str]:
        concurrency_limit = max(1, min(int(concurrency_limit or 1), 10))
        with self._lock:
            chosen = self._pick_active_token_locked(
                strategy=strategy, concurrency_limit=concurrency_limit
            )
            if chosen is not None:
                self._acquire_token_lease(chosen)
                self.save()
                return chosen["value"]

            # Auto-revive one recoverable token after cooldown.
            now_ts = time.time()
            recoverable = [
                t
                for t in self.tokens
                if t["status"] == "error"
                and self._token_has_capacity(t, concurrency_limit)
                and float(t.get("error_until", 0) or 0) <= now_ts
            ]
            if not recoverable:
                return None
            recoverable.sort(key=lambda x: x["fails"])
            chosen = recoverable[0]
            chosen["status"] = "active"
            chosen["fails"] = max(0, int(chosen.get("fails", 0)) - 1)
            chosen["error_until"] = 0
            self.save()
            picked = self._pick_active_token_locked(
                strategy=strategy, concurrency_limit=concurrency_limit
            )
            leased = picked or chosen
            self._acquire_token_lease(leased)
            self.save()
            return leased["value"]

    @classmethod
    def _release_token_lease(cls, token: Dict):
        remaining = max(0, cls._token_lease_count(token) - 1)
        token.pop("lease_id", None)
        if remaining:
            token["lease_count"] = remaining
        else:
            token.pop("lease_count", None)
            token.pop("leased_at", None)

    def release(self, value: str) -> Optional[Dict]:
        updated = None
        with self._lock:
            t = self._value_index.get(self._normalize_token_value(value))
            if t is not None:
                self._release_token_lease(t)
                updated = dict(t)
                try:
                    self._store.update_tokens([updated])
                except Exception:
                    self.save()
        return updated

    def report_exhausted(self, value: str) -> Optional[Dict]:
        updated = None
        now_ts = time.time()
        with self._lock:
            t = self._value_index.get(self._normalize_token_value(value))
            if t is not None:
                t["status"] = "exhausted"
                t["error_until"] = 0
                t["updated_at"] = now_ts
                t.setdefault("exhausted_at", now_ts)
                self._release_token_lease(t)
                updated = dict(t)
            self.save()
        return updated

    def _report_status_by_identity(
        self,
        status: str,
        *,
        token_id: str = "",
        token_account_email: str = "",
        token_account_name: str = "",
    ) -> Optional[Dict]:
        token_id = str(token_id or "").strip()
        email = str(token_account_email or "").strip().casefold()
        name = str(token_account_name or "").strip().casefold()

        updated = None
        with self._lock:
            target = self._id_index.get(token_id) if token_id else None
            match_reason = "token_id" if target is not None else ""

            if target is None and email:
                matches = [
                    t
                    for t in self.tokens
                    if isinstance(t, dict)
                    and str(t.get("refresh_profile_email") or "").strip().casefold()
                    == email
                ]
                if len(matches) == 1:
                    target = matches[0]
                    match_reason = "token_account_email"

            if target is None and name:
                matches = [
                    t
                    for t in self.tokens
                    if isinstance(t, dict)
                    and str(t.get("refresh_profile_name") or "").strip().casefold()
                    == name
                ]
                if len(matches) == 1:
                    target = matches[0]
                    match_reason = "token_account_name"

            if target is not None:
                now_ts = time.time()
                previous_status = str(target.get("status") or "active")
                target["status"] = status
                target["error_until"] = 0
                target["updated_at"] = now_ts
                self._release_token_lease(target)
                if status == "exhausted":
                    target.setdefault("exhausted_at", now_ts)
                updated = dict(target)
                updated["_previous_status"] = previous_status
                updated["_matched_by"] = match_reason

            self.save()
        return updated

    def report_exhausted_by_identity(
        self,
        *,
        token_id: str = "",
        token_account_email: str = "",
        token_account_name: str = "",
    ) -> Optional[Dict]:
        return self._report_status_by_identity(
            "exhausted",
            token_id=token_id,
            token_account_email=token_account_email,
            token_account_name=token_account_name,
        )

    def report_invalid_by_identity(
        self,
        *,
        token_id: str = "",
        token_account_email: str = "",
        token_account_name: str = "",
    ) -> Optional[Dict]:
        return self._report_status_by_identity(
            "invalid",
            token_id=token_id,
            token_account_email=token_account_email,
            token_account_name=token_account_name,
        )

    def report_abnormal_by_identity(
        self,
        *,
        token_id: str = "",
        token_account_email: str = "",
        token_account_name: str = "",
    ) -> Optional[Dict]:
        return self._report_status_by_identity(
            "abnormal",
            token_id=token_id,
            token_account_email=token_account_email,
            token_account_name=token_account_name,
        )

    def report_invalid(self, value: str) -> Optional[Dict]:
        updated = None
        now_ts = time.time()
        with self._lock:
            t = self._value_index.get(self._normalize_token_value(value))
            if t is not None:
                t["status"] = "invalid"
                t["error_until"] = 0
                t["updated_at"] = now_ts
                self._release_token_lease(t)
                updated = dict(t)
            self.save()
        return updated

    def mark_expired_active_tokens_invalid(self, limit: int = 200) -> Dict:
        now_ts = int(time.time())
        try:
            candidates = self._store.list_earliest_expiring_active_tokens(limit=limit)
        except Exception:
            candidates = []

        changed: List[Dict] = []
        checked = 0
        with self._lock:
            for candidate in candidates:
                tid = str(candidate.get("id") or "").strip()
                if not tid:
                    continue
                token = self._id_index.get(tid)
                if not isinstance(token, dict):
                    continue
                if str(token.get("status") or "active").strip().lower() != "active":
                    continue

                exp_ts = self._decode_jwt_exp(str(token.get("value") or ""))
                if exp_ts is None:
                    continue
                checked += 1
                if int(exp_ts) >= now_ts:
                    continue

                token["status"] = "invalid"
                token["error_until"] = 0
                token["updated_at"] = now_ts
                changed.append(dict(token))

            if changed:
                try:
                    self._store.update_tokens(changed)
                except Exception:
                    self.save()

        return {
            "checked": checked,
            "marked_invalid": len(changed),
            "limit": max(1, int(limit or 200)),
        }

    def start_expired_token_scanner(
        self,
        *,
        interval_seconds: int = 300,
        batch_limit: int = 200,
    ) -> None:
        with self._lock:
            if self._expired_scanner_started:
                return
            self._expired_scanner_started = True

        interval = max(60, int(interval_seconds or 300))
        limit = max(1, int(batch_limit or 200))

        def worker():
            while not self._expired_scanner_stop_event.is_set():
                try:
                    result = self.mark_expired_active_tokens_invalid(limit=limit)
                    marked = int(result.get("marked_invalid") or 0)
                    if marked:
                        logger.info(
                            "expired token scanner marked invalid count=%s checked=%s limit=%s",
                            marked,
                            result.get("checked"),
                            result.get("limit"),
                        )
                except Exception:
                    logger.exception("expired token scanner failed")
                self._expired_scanner_stop_event.wait(interval)

        threading.Thread(
            target=worker,
            name="token-expired-scanner",
            daemon=True,
        ).start()

    def report_error(self, value: str):
        with self._lock:
            t = self._value_index.get(self._normalize_token_value(value))
            if t is not None:
                t["fails"] += 1
                t["status"] = "error"
                t["error_until"] = time.time() + self.ERROR_COOLDOWN_SECONDS
                self._release_token_lease(t)
            self.save()

    def report_success(self, value: str):
        with self._lock:
            t = self._value_index.get(self._normalize_token_value(value))
            if t is not None:
                t["fails"] = max(0, int(t.get("fails", 0)) - 1)
                t["success_count"] = max(0, int(t.get("success_count", 0))) + 1
                if t["status"] == "error":
                    t["status"] = "active"
                    t["error_until"] = 0
                self._release_token_lease(t)
            self.save()

    def report_success_with_auto_disable(
        self,
        value: str,
        *,
        auto_disable_enabled: bool = False,
        auto_disable_threshold: int = 0,
    ) -> Optional[Dict]:
        with self._lock:
            t = self._value_index.get(self._normalize_token_value(value))
            if t is None:
                self.save()
                return None
            t["fails"] = max(0, int(t.get("fails", 0)) - 1)
            success_count = max(0, int(t.get("success_count", 0))) + 1
            t["success_count"] = success_count
            if t["status"] == "error":
                t["status"] = "active"
                t["error_until"] = 0
            disabled_by_limit = False
            try:
                threshold = int(auto_disable_threshold or 0)
            except Exception:
                threshold = 0
            if bool(auto_disable_enabled) and threshold > 0 and success_count >= threshold:
                now_ts = time.time()
                t["status"] = "exhausted"
                t["error_until"] = 0
                t["updated_at"] = now_ts
                t.setdefault("exhausted_at", now_ts)
                disabled_by_limit = True
            self._release_token_lease(t)
            self.save()
            result = dict(t)
            result["_disabled_by_success_limit"] = disabled_by_limit
            result["_success_count"] = success_count
            return result

    def overwrite_success_counts(
        self,
        *,
        counts_by_token_id: Dict[str, int],
        counts_by_email: Optional[Dict[str, int]] = None,
        counts_by_name: Optional[Dict[str, int]] = None,
        auto_disable_enabled: bool = False,
        auto_disable_threshold: int = 0,
    ) -> Dict:
        normalized_by_id = {
            str(k or "").strip(): max(0, int(v or 0))
            for k, v in dict(counts_by_token_id or {}).items()
            if str(k or "").strip()
        }
        normalized_by_email = {
            str(k or "").strip().casefold(): max(0, int(v or 0))
            for k, v in dict(counts_by_email or {}).items()
            if str(k or "").strip()
        }
        normalized_by_name = {
            str(k or "").strip().casefold(): max(0, int(v or 0))
            for k, v in dict(counts_by_name or {}).items()
            if str(k or "").strip()
        }
        try:
            threshold = int(auto_disable_threshold or 0)
        except Exception:
            threshold = 0

        with self._lock:
            email_matches: Dict[str, List[Dict]] = {}
            name_matches: Dict[str, List[Dict]] = {}
            for token in self.tokens:
                if not isinstance(token, dict):
                    continue
                email = str(token.get("refresh_profile_email") or "").strip().casefold()
                name = str(token.get("refresh_profile_name") or "").strip().casefold()
                if email:
                    email_matches.setdefault(email, []).append(token)
                if name:
                    name_matches.setdefault(name, []).append(token)

            matched_token_ids = set()
            matched_by_id = 0
            matched_by_email = 0
            matched_by_name = 0
            changed_tokens = 0
            reset_to_zero_tokens = 0
            exhausted_by_threshold = 0
            total_success_count = 0
            exhausted_profile_ids: set[str] = set()

            for token in self.tokens:
                if not isinstance(token, dict):
                    continue
                token_id = self._token_id_key(token)
                email = str(token.get("refresh_profile_email") or "").strip().casefold()
                name = str(token.get("refresh_profile_name") or "").strip().casefold()
                old_count = max(0, int(token.get("success_count", 0) or 0))
                new_count = 0
                matched = False

                if token_id and token_id in normalized_by_id:
                    new_count = normalized_by_id[token_id]
                    matched = True
                    matched_by_id += 1
                elif email and len(email_matches.get(email) or []) == 1:
                    new_count = normalized_by_email.get(email, 0)
                    matched = email in normalized_by_email
                    if matched:
                        matched_by_email += 1
                elif name and len(name_matches.get(name) or []) == 1:
                    new_count = normalized_by_name.get(name, 0)
                    matched = name in normalized_by_name
                    if matched:
                        matched_by_name += 1

                token["success_count"] = new_count
                total_success_count += new_count
                if matched and token_id:
                    matched_token_ids.add(token_id)
                if old_count != new_count:
                    changed_tokens += 1
                if old_count > 0 and new_count == 0:
                    reset_to_zero_tokens += 1
                if (
                    bool(auto_disable_enabled)
                    and threshold > 0
                    and new_count >= threshold
                    and str(token.get("status") or "").strip().lower()
                    not in {"invalid"}
                ):
                    if str(token.get("status") or "").strip().lower() != "exhausted":
                        exhausted_by_threshold += 1
                    now_ts = time.time()
                    token["status"] = "exhausted"
                    token["error_until"] = 0
                    token["updated_at"] = now_ts
                    token.setdefault("exhausted_at", now_ts)
                    profile_id = str(token.get("refresh_profile_id") or "").strip()
                    if bool(token.get("auto_refresh")) and profile_id:
                        exhausted_profile_ids.add(profile_id)

            self.save()
            nonzero_tokens = sum(
                1
                for token in self.tokens
                if isinstance(token, dict)
                and int(token.get("success_count", 0) or 0) > 0
            )
            return {
                "total_tokens": len([t for t in self.tokens if isinstance(t, dict)]),
                "matched_tokens": len(matched_token_ids)
                if matched_token_ids
                else (matched_by_id + matched_by_email + matched_by_name),
                "matched_by_token_id": matched_by_id,
                "matched_by_email": matched_by_email,
                "matched_by_name": matched_by_name,
                "changed_tokens": changed_tokens,
                "reset_to_zero_tokens": reset_to_zero_tokens,
                "nonzero_success_tokens": nonzero_tokens,
                "total_success_count": total_success_count,
                "exhausted_by_threshold": exhausted_by_threshold,
                "exhausted_profile_ids": sorted(exhausted_profile_ids),
            }

    @staticmethod
    def _decode_jwt_payload(value: str) -> Optional[dict]:
        token = str(value or "").strip()
        parts = token.split(".")
        if len(parts) < 2:
            return None
        payload = parts[1]
        payload += "=" * ((4 - len(payload) % 4) % 4)
        try:
            raw = base64.urlsafe_b64decode(payload.encode("utf-8"))
            data = json.loads(raw.decode("utf-8", errors="ignore"))
            if isinstance(data, dict):
                return data
        except Exception:
            return None
        return None

    @classmethod
    def _decode_jwt_exp(cls, value: str) -> Optional[int]:
        data = cls._decode_jwt_payload(value)
        if not data:
            return None

        exp = data.get("exp")
        if isinstance(exp, (int, float)):
            return int(exp)

        # Adobe tokens often expose created_at + expires_in in payload instead of exp.
        created_at = data.get("created_at")
        expires_in = data.get("expires_in")
        try:
            created_at_val = int(str(created_at).strip())
            expires_in_val = int(str(expires_in).strip())
        except Exception:
            return None

        if created_at_val <= 0 or expires_in_val <= 0:
            return None

        # Some fields are milliseconds (e.g. 1771862511913 / 86400000)
        if created_at_val > 10_000_000_000:
            created_at_val = int(created_at_val / 1000)
        if expires_in_val > 86400 * 2:
            expires_in_val = int(expires_in_val / 1000)

        return created_at_val + expires_in_val

    @staticmethod
    def _token_sort_ts(token: Dict) -> float:
        for key in ("updated_at", "added_at"):
            try:
                value = float(token.get(key) or 0)
            except Exception:
                value = 0
            if value > 0:
                return value
        return 0

    @staticmethod
    def _token_matches_filters(token: Dict, status: str = "", credits: str = "") -> bool:
        status_filter = str(status or "").strip().lower()
        credits_filter = str(credits or "").strip().lower()
        if status_filter and str(token.get("status") or "").strip().lower() != status_filter:
            return False
        if credits_filter == "error" and not str(token.get("credits_error") or "").strip():
            return False
        return True

    def _public_token_locked(self, token: Dict, now_ts: int) -> Dict:
        val = str(token.get("value") or "")
        masked = val[:15] + "..." + val[-10:] if len(val) > 30 else "***"
        exp_ts = self._decode_jwt_exp(val)
        remaining_seconds = None
        exp_readable = None
        if exp_ts is not None:
            remaining_seconds = exp_ts - now_ts
            try:
                exp_readable = datetime.fromtimestamp(exp_ts).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            except Exception:
                exp_readable = str(exp_ts)
        return {
            "id": token.get("id"),
            "value": masked,
            "status": token.get("status", "active"),
            "fails": token.get("fails", 0),
            "success_count": token.get("success_count", 0),
            "added_at": token.get("added_at", 0),
            "updated_at": token.get("updated_at"),
            "exhausted_at": token.get("exhausted_at"),
            "error_until": token.get("error_until", 0),
            "source": token.get("source", "manual"),
            "auto_refresh": bool(token.get("auto_refresh", False)),
            "refresh_profile_id": token.get("refresh_profile_id"),
            "refresh_profile_name": token.get("refresh_profile_name"),
            "refresh_profile_email": token.get("refresh_profile_email"),
            "credits_total": token.get("credits_total"),
            "credits_used": token.get("credits_used"),
            "credits_available": token.get("credits_available"),
            "credits_available_until": token.get("credits_available_until"),
            "credits_updated_at": token.get("credits_updated_at"),
            "credits_error": token.get("credits_error", ""),
            "expires_at": exp_ts,
            "expires_at_text": exp_readable,
            "remaining_seconds": remaining_seconds,
            "is_expired": bool(
                exp_ts is not None
                and remaining_seconds is not None
                and remaining_seconds <= 0
            ),
        }

    def count(self) -> int:
        with self._lock:
            return len(self.tokens)

    def storage_info(self) -> Dict:
        db_path = self._store.db_path
        with self._lock:
            return {
                "backend": "sqlite",
                "db_path": str(db_path),
                "db_exists": db_path.exists(),
                "db_size_bytes": db_path.stat().st_size if db_path.exists() else 0,
                "tokens": len([t for t in self.tokens if isinstance(t, dict)]),
            }

    def list_page(
        self,
        page: int = 1,
        page_size: int = 50,
        status: str = "",
        credits: str = "",
    ) -> Dict:
        try:
            return self._list_page_sqlite(
                page=page,
                page_size=page_size,
                status=status,
                credits=credits,
            )
        except Exception as exc:
            logger.warning("token list sqlite failed, falling back to memory: %s", exc)
            return self._list_page_memory(
                page=page,
                page_size=page_size,
                status=status,
                credits=credits,
            )

    def _list_page_sqlite(
        self,
        page: int = 1,
        page_size: int = 50,
        status: str = "",
        credits: str = "",
    ) -> Dict:
        started = time.perf_counter()
        payload = self._store.list_tokens_page(
            page=page,
            page_size=page_size,
            status=status,
            credits=credits,
        )
        now_ts = int(time.time())
        tokens = [
            self._public_token_locked(token, now_ts)
            for token in payload.get("tokens", [])
            if isinstance(token, dict)
        ]
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        result = {
            "tokens": tokens,
            "summary": payload.get("summary") or {},
            "pagination": payload.get("pagination") or {},
            "backend": "sqlite",
            "duration_ms": elapsed_ms,
        }
        return result

    def _list_page_memory(
        self,
        page: int = 1,
        page_size: int = 50,
        status: str = "",
        credits: str = "",
    ) -> Dict:
        started = time.perf_counter()
        page = max(1, int(page or 1))
        page_size = max(1, min(200, int(page_size or 50)))
        with self._lock:
            total_count = len(self.tokens)
            active_count = sum(
                1
                for token in self.tokens
                if str(token.get("status") or "").strip().lower() == "active"
            )
            filtered = [
                token
                for token in self.tokens
                if isinstance(token, dict)
                and self._token_matches_filters(token, status=status, credits=credits)
            ]
            filtered.sort(key=self._token_sort_ts, reverse=True)
            filtered_count = len(filtered)
            total_pages = max(1, (filtered_count + page_size - 1) // page_size)
            page = min(page, total_pages)
            start = (page - 1) * page_size
            page_items = filtered[start : start + page_size]
            now_ts = int(time.time())
            return {
                "tokens": [
                    self._public_token_locked(token, now_ts) for token in page_items
                ],
                "summary": {
                    "total": total_count,
                    "active": active_count,
                    "filtered": filtered_count,
                },
                "pagination": {
                    "page": page,
                    "page_size": page_size,
                    "total": filtered_count,
                    "total_pages": total_pages,
                },
                "backend": "memory",
                "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            }

    def list_all(self):
        with self._lock:
            sorted_tokens = sorted(
                [token for token in self.tokens if isinstance(token, dict)],
                key=self._token_sort_ts,
                reverse=True,
            )
            now_ts = int(time.time())
            return [
                self._public_token_locked(token, now_ts) for token in sorted_tokens
            ]

    def export_tokens(self, ids: Optional[List[str]] = None) -> List[Dict]:
        selected_ids = None
        if isinstance(ids, list):
            normalized = [str(x or "").strip() for x in ids]
            selected_ids = {x for x in normalized if x}
        with self._lock:
            out: List[Dict] = []
            for t in self.tokens:
                tid = str(t.get("id") or "").strip()
                if selected_ids is not None and tid not in selected_ids:
                    continue
                out.append(
                    {
                        "id": tid,
                        "token": str(t.get("value") or "").strip(),
                        "status": str(t.get("status") or "active"),
                        "source": str(t.get("source") or "manual"),
                        "auto_refresh": bool(t.get("auto_refresh", False)),
                        "refresh_profile_id": t.get("refresh_profile_id"),
                        "refresh_profile_name": t.get("refresh_profile_name"),
                        "refresh_profile_email": t.get("refresh_profile_email"),
                        "added_at": t.get("added_at"),
                        "updated_at": t.get("updated_at"),
                        "exhausted_at": t.get("exhausted_at"),
                    }
                )
            return out


token_manager = TokenManager()
