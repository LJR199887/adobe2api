from __future__ import annotations

import json
import threading
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional


@dataclass
class JobRecord:
    id: str
    prompt: str
    aspect_ratio: str
    model: Optional[str] = None
    kind: Optional[str] = None
    status: str = "queued"
    progress: float = 0.0
    image_url: Optional[str] = None
    error: Optional[str] = None
    created_at: float = 0.0
    updated_at: float = 0.0


class JobStore:
    def __init__(self, max_items: int = 200) -> None:
        self._items: dict[str, JobRecord] = {}
        self._lock = threading.Lock()
        self._max_items = max_items

    def _cleanup(self):
        if len(self._items) > self._max_items:
            sorted_items = sorted(self._items.values(), key=lambda x: x.created_at)
            for item in sorted_items[:50]:
                self._items.pop(item.id, None)

    def create(
        self,
        prompt: str,
        aspect_ratio: str,
        model: Optional[str] = None,
        kind: Optional[str] = None,
    ) -> JobRecord:
        now = time.time()
        item = JobRecord(
            id=uuid.uuid4().hex,
            prompt=prompt,
            aspect_ratio=aspect_ratio,
            model=model,
            kind=kind,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._cleanup()
            self._items[item.id] = item
        return item

    def get(self, job_id: str) -> Optional[JobRecord]:
        with self._lock:
            return self._items.get(job_id)

    def update(self, job_id: str, **kwargs) -> None:
        with self._lock:
            item = self._items.get(job_id)
            if not item:
                return
            for k, v in kwargs.items():
                setattr(item, k, v)
            item.updated_at = time.time()


@dataclass
class RequestLogRecord:
    id: str
    ts: float
    method: str
    path: str
    status_code: int
    duration_sec: int
    operation: str
    request_id: Optional[str] = None
    preview_url: Optional[str] = None
    preview_kind: Optional[str] = None
    model: Optional[str] = None
    model_params: Optional[str] = None
    prompt_preview: Optional[str] = None
    error: Optional[str] = None
    error_code: Optional[str] = None
    task_status: Optional[str] = None
    task_progress: Optional[float] = None
    upstream_job_id: Optional[str] = None
    retry_after: Optional[int] = None
    token_id: Optional[str] = None
    token_account_name: Optional[str] = None
    token_account_email: Optional[str] = None
    token_source: Optional[str] = None
    token_attempt: Optional[int] = None


class RequestLogStore:
    _FAILED_TASK_STATUSES = {"FAILED", "ERROR", "CANCELLED"}
    _GENERATION_OPERATIONS = {
        "api.generate",
        "chat.completions",
        "images.generations",
        "video.generations",
    }
    _GENERATION_PATHS = {
        "/api/v1/generate",
        "/v1/chat/completions",
        "/v1/images/generations",
        "/v1/video/generations",
    }

    def __init__(self, file_path: Path, max_items: int = 500) -> None:
        self._file_path = file_path
        self._lock = threading.Lock()
        self._max_items = max_items
        self._append_since_truncate = 0
        self._truncate_check_interval = 200
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        if not self._file_path.exists():
            self._file_path.touch()

    def _truncate_to_max_locked(self) -> None:
        tail: deque[str] = deque(maxlen=self._max_items)
        total = 0
        with self._file_path.open("r", encoding="utf-8") as f:
            for line in f:
                total += 1
                tail.append(line)
        if total <= self._max_items:
            return
        with self._file_path.open("w", encoding="utf-8") as f:
            f.writelines(tail)

    def _append_payload_locked(self, payload: dict) -> None:
        with self._file_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self._append_since_truncate += 1
        if self._append_since_truncate >= self._truncate_check_interval:
            self._truncate_to_max_locked()
            self._append_since_truncate = 0

    def _read_payloads_locked(self) -> list[dict]:
        items: list[dict] = []
        with self._file_path.open("r", encoding="utf-8") as f:
            for line in f:
                raw = line.strip()
                if not raw:
                    continue
                try:
                    item = json.loads(raw)
                except Exception:
                    continue
                if isinstance(item, dict):
                    items.append(item)
        return items

    @staticmethod
    def _get_account_values(item: dict) -> list[str]:
        values: list[str] = []
        if not isinstance(item, dict):
            return values
        for key in ("token_account_email", "token_account_name", "token_id"):
            text = str(item.get(key) or "").strip()
            if text:
                values.append(text)
        return values

    @classmethod
    def _match_account_filter(cls, item: dict, account: str) -> bool:
        target = str(account or "").strip().casefold()
        if not target:
            return True
        for value in cls._get_account_values(item):
            if value.casefold() == target:
                return True
        return False

    @classmethod
    def _is_generation_request(cls, item: dict) -> bool:
        operation = str(item.get("operation") or "").strip()
        path = str(item.get("path") or "").strip()
        return operation in cls._GENERATION_OPERATIONS or path in cls._GENERATION_PATHS

    @staticmethod
    def _is_token_invalid_or_expired_error(item: dict) -> bool:
        message = str(item.get("error") or "").strip().casefold()
        return "token invalid or expired" in message

    @classmethod
    def _is_token_invalid_backfill_candidate(cls, item: dict) -> bool:
        if cls._is_token_invalid_or_expired_error(item):
            return True
        try:
            status_code = int(item.get("status_code") or 0)
        except Exception:
            status_code = 0
        return status_code == 401

    @classmethod
    def _is_failed_item(cls, item: dict) -> bool:
        if not isinstance(item, dict):
            return False
        try:
            status_code = int(item.get("status_code") or 0)
        except Exception:
            status_code = 0
        if status_code >= 400:
            return True

        task_status = str(item.get("task_status") or "").upper()
        if task_status in cls._FAILED_TASK_STATUSES:
            return True
        if task_status == "IN_PROGRESS":
            return False

        preview_url = str(item.get("preview_url") or "").strip()
        if 200 <= status_code < 300 and cls._is_generation_request(item):
            return not bool(preview_url)
        return False

    @staticmethod
    def _resolve_media_kind(item: dict) -> str:
        if not isinstance(item, dict):
            return ""

        preview_kind = str(item.get("preview_kind") or "").strip().lower()
        if preview_kind in {"image", "video"}:
            return preview_kind

        model = str(item.get("model") or "").strip().lower()
        if model:
            if (
                "sora" in model
                or "veo" in model
                or "video" in model
                or "text2video" in model
            ):
                return "video"
            return "image"

        path = str(item.get("path") or "").strip().lower()
        operation = str(item.get("operation") or "").strip().lower()
        if path.endswith("/v1/video/generations") or operation == "video.generations":
            return "video"
        if path.endswith("/v1/images/generations") or operation == "images.generations":
            return "image"
        if path.endswith("/v1/chat/completions") or operation == "chat.completions":
            return "image"
        return ""

    @classmethod
    def _apply_filters(
        cls,
        items: list[dict],
        *,
        start_ts: Optional[float] = None,
        end_ts: Optional[float] = None,
        failed_only: bool = False,
        account: str = "",
        media_kind: str = "",
    ) -> list[dict]:
        filtered: list[dict] = []
        normalized_media_kind = str(media_kind or "").strip().lower()
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                ts_val = float(item.get("ts") or 0)
            except Exception:
                ts_val = 0.0
            if start_ts is not None and ts_val < float(start_ts):
                continue
            if end_ts is not None and ts_val > float(end_ts):
                continue

            if failed_only and not cls._is_failed_item(item):
                continue
            if account and not cls._match_account_filter(item, account):
                continue
            if normalized_media_kind:
                if cls._resolve_media_kind(item) != normalized_media_kind:
                    continue
            filtered.append(item)
        return filtered

    def add(self, item: RequestLogRecord) -> None:
        payload = asdict(item)
        self.add_payload(payload)

    def add_payload(self, payload: dict) -> None:
        if not isinstance(payload, dict):
            return
        with self._lock:
            self._append_payload_locked(payload)

    def upsert(self, item_id: str, payload: dict) -> None:
        if not item_id:
            return
        if not isinstance(payload, dict):
            return
        item = {"id": item_id}
        item.update(payload)
        with self._lock:
            self._append_payload_locked(item)

    def list(
        self,
        limit: int = 20,
        page: int = 1,
        *,
        failed_only: bool = False,
        account: str = "",
        media_kind: str = "",
        start_ts: Optional[float] = None,
        end_ts: Optional[float] = None,
    ) -> tuple[list[dict], int]:
        safe_limit = min(max(int(limit or 20), 1), 100)
        safe_page = max(int(page or 1), 1)
        with self._lock:
            items = self._read_payloads_locked()

        filtered = self._apply_filters(
            items,
            start_ts=start_ts,
            end_ts=end_ts,
            failed_only=failed_only,
            account=account,
            media_kind=media_kind,
        )
        total = len(filtered)
        if total <= 0:
            return [], 0

        end_idx = total - ((safe_page - 1) * safe_limit)
        if end_idx <= 0:
            return [], total

        start_idx = max(0, end_idx - safe_limit)
        selected = filtered[start_idx:end_idx]
        return list(reversed(selected)), total

    def list_failed_accounts(
        self,
        *,
        limit: int = 200,
        start_ts: Optional[float] = None,
        end_ts: Optional[float] = None,
    ) -> list[dict]:
        safe_limit = min(max(int(limit or 200), 1), 500)
        with self._lock:
            items = self._read_payloads_locked()

        filtered = self._apply_filters(
            items,
            start_ts=start_ts,
            end_ts=end_ts,
            failed_only=True,
        )
        grouped: dict[str, dict] = {}
        for item in filtered:
            email = str(item.get("token_account_email") or "").strip()
            name = str(item.get("token_account_name") or "").strip()
            token_id = str(item.get("token_id") or "").strip()
            account_key = email or name or token_id
            if not account_key:
                continue
            try:
                ts_val = float(item.get("ts") or 0)
            except Exception:
                ts_val = 0.0
            bucket = grouped.get(account_key)
            if bucket is None:
                grouped[account_key] = {
                    "account_key": account_key,
                    "token_account_email": email or None,
                    "token_account_name": name or None,
                    "token_id": token_id or None,
                    "failed_count": 1,
                    "last_ts": ts_val,
                }
                continue
            bucket["failed_count"] = int(bucket.get("failed_count") or 0) + 1
            if ts_val > float(bucket.get("last_ts") or 0):
                bucket["last_ts"] = ts_val
            if email and not bucket.get("token_account_email"):
                bucket["token_account_email"] = email
            if name and not bucket.get("token_account_name"):
                bucket["token_account_name"] = name
            if token_id and not bucket.get("token_id"):
                bucket["token_id"] = token_id

        items_out = list(grouped.values())
        items_out.sort(
            key=lambda x: (
                -int(x.get("failed_count") or 0),
                -float(x.get("last_ts") or 0),
                str(x.get("account_key") or "").casefold(),
            )
        )
        return items_out[:safe_limit]

    def find_poll_invalid_token_candidates(self, *, limit: int = 500) -> dict:
        safe_limit = min(max(int(limit or 500), 1), 5000)
        with self._lock:
            items = self._read_payloads_locked()

        latest_by_id: dict[str, dict] = {}
        anonymous_items: list[dict] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or "").strip()
            if item_id:
                latest_by_id[item_id] = item
            else:
                anonymous_items.append(item)

        deduped_items = [*latest_by_id.values(), *anonymous_items]
        grouped: dict[str, dict] = {}
        matched_logs = 0
        unidentified_logs = 0

        for item in deduped_items:
            if not isinstance(item, dict):
                continue
            if not self._is_generation_request(item):
                continue
            if not self._is_token_invalid_backfill_candidate(item):
                continue
            upstream_job_id = str(item.get("upstream_job_id") or "").strip()

            token_id = str(item.get("token_id") or "").strip()
            email = str(item.get("token_account_email") or "").strip()
            name = str(item.get("token_account_name") or "").strip()
            account_key = token_id or email or name
            if not account_key:
                unidentified_logs += 1
                continue

            matched_logs += 1
            try:
                ts_val = float(item.get("ts") or 0)
            except Exception:
                ts_val = 0.0

            bucket = grouped.get(account_key)
            if bucket is None:
                bucket = {
                    "account_key": account_key,
                    "token_id": token_id or None,
                    "token_account_email": email or None,
                    "token_account_name": name or None,
                    "matched_log_count": 0,
                    "first_ts": ts_val,
                    "last_ts": ts_val,
                    "upstream_job_ids": [],
                    "has_upstream_job_id": bool(upstream_job_id),
                    "matched_reasons": [],
                }
                grouped[account_key] = bucket

            bucket["matched_log_count"] = int(bucket.get("matched_log_count") or 0) + 1
            bucket["first_ts"] = min(float(bucket.get("first_ts") or ts_val), ts_val)
            bucket["last_ts"] = max(float(bucket.get("last_ts") or ts_val), ts_val)
            if upstream_job_id:
                bucket["has_upstream_job_id"] = True
            if token_id and not bucket.get("token_id"):
                bucket["token_id"] = token_id
            if email and not bucket.get("token_account_email"):
                bucket["token_account_email"] = email
            if name and not bucket.get("token_account_name"):
                bucket["token_account_name"] = name
            reason = (
                "token_invalid_or_expired"
                if self._is_token_invalid_or_expired_error(item)
                else "http_401"
            )
            reasons = bucket.get("matched_reasons")
            if isinstance(reasons, list) and reason not in reasons:
                reasons.append(reason)
            if upstream_job_id:
                job_ids = bucket.get("upstream_job_ids")
                if isinstance(job_ids, list) and upstream_job_id not in job_ids:
                    job_ids.append(upstream_job_id)

        candidates = list(grouped.values())
        candidates.sort(
            key=lambda x: (
                -float(x.get("last_ts") or 0),
                str(x.get("account_key") or "").casefold(),
            )
        )
        for candidate in candidates:
            job_ids = candidate.get("upstream_job_ids")
            if isinstance(job_ids, list):
                candidate["upstream_job_ids"] = job_ids[:10]

        return {
            "scanned_logs": len(deduped_items),
            "matched_logs": matched_logs,
            "unidentified_logs": unidentified_logs,
            "candidate_count": len(candidates),
            "candidates": candidates[:safe_limit],
        }

    def get(self, request_id: str) -> Optional[dict]:
        target = str(request_id or "").strip()
        if not target:
            return None
        with self._lock:
            with self._file_path.open("r", encoding="utf-8") as f:
                lines = f.readlines()

        fallback = None
        attempt_prefix = f"{target}-a"
        for line in reversed(lines):
            raw = line.strip()
            if not raw:
                continue
            try:
                item = json.loads(raw)
            except Exception:
                continue
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or "").strip()
            item_request_id = str(item.get("request_id") or "").strip()
            if item_id == target:
                payload = dict(item)
                payload.setdefault("request_id", target)
                return payload
            if item_request_id == target:
                return dict(item)
            if fallback is None and item_id.startswith(attempt_prefix):
                payload = dict(item)
                payload.setdefault("request_id", target)
                payload.setdefault("attempt_id", item_id)
                fallback = payload
        return fallback

    def stats(
        self,
        start_ts: Optional[float] = None,
        end_ts: Optional[float] = None,
    ) -> dict:
        total_requests = 0
        failed_requests = 0
        generated_images = 0
        generated_videos = 0
        in_progress_requests = 0

        with self._lock:
            items = self._read_payloads_locked()

        filtered = self._apply_filters(items, start_ts=start_ts, end_ts=end_ts)
        for item in filtered:
            if not isinstance(item, dict):
                continue

            total_requests += 1

            try:
                status_code = int(item.get("status_code") or 0)
            except Exception:
                status_code = 0
            failed = self._is_failed_item(item)
            if failed:
                failed_requests += 1

            task_status = str(item.get("task_status") or "").upper()
            if task_status == "IN_PROGRESS":
                in_progress_requests += 1

            preview_kind = str(item.get("preview_kind") or "").strip().lower()
            if 200 <= status_code < 300 and not failed:
                if preview_kind == "image":
                    generated_images += 1
                elif preview_kind == "video":
                    generated_videos += 1

        return {
            "total_requests": total_requests,
            "failed_requests": failed_requests,
            "generated_images": generated_images,
            "generated_videos": generated_videos,
            "generated_total": generated_images + generated_videos,
            "in_progress_requests": in_progress_requests,
        }

    def clear(self) -> None:
        with self._lock:
            with self._file_path.open("w", encoding="utf-8") as f:
                f.write("")
            self._append_since_truncate = 0

    def compute_generation_success_counts(self) -> dict:
        with self._lock:
            items = self._read_payloads_locked()

        latest_by_id: dict[str, dict] = {}
        anonymous_items: list[dict] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or "").strip()
            if item_id:
                latest_by_id[item_id] = item
            else:
                anonymous_items.append(item)

        deduped_items = [*latest_by_id.values(), *anonymous_items]
        counts_by_token_id: dict[str, int] = {}
        counts_by_email: dict[str, int] = {}
        counts_by_name: dict[str, int] = {}
        scanned_logs = len(deduped_items)
        generation_logs = 0
        success_logs = 0
        unidentified_success_logs = 0

        for item in deduped_items:
            if not isinstance(item, dict) or not self._is_generation_request(item):
                continue
            generation_logs += 1
            if self._is_failed_item(item):
                continue
            try:
                status_code = int(item.get("status_code") or 0)
            except Exception:
                status_code = 0
            if not (200 <= status_code < 300):
                continue

            success_logs += 1
            token_id = str(item.get("token_id") or "").strip()
            email = str(item.get("token_account_email") or "").strip().casefold()
            name = str(item.get("token_account_name") or "").strip().casefold()
            identified = False
            if token_id:
                counts_by_token_id[token_id] = counts_by_token_id.get(token_id, 0) + 1
                identified = True
            if email:
                counts_by_email[email] = counts_by_email.get(email, 0) + 1
                identified = True
            if name:
                counts_by_name[name] = counts_by_name.get(name, 0) + 1
                identified = True
            if not identified:
                unidentified_success_logs += 1

        return {
            "scanned_logs": scanned_logs,
            "generation_logs": generation_logs,
            "success_logs": success_logs,
            "unidentified_success_logs": unidentified_success_logs,
            "counts_by_token_id": counts_by_token_id,
            "counts_by_email": counts_by_email,
            "counts_by_name": counts_by_name,
        }


@dataclass
class ErrorDetailRecord:
    code: str
    ts: float
    message: str
    error_type: Optional[str] = None
    status_code: Optional[int] = None
    operation: Optional[str] = None
    method: Optional[str] = None
    path: Optional[str] = None
    log_id: Optional[str] = None
    model: Optional[str] = None
    prompt_preview: Optional[str] = None
    task_status: Optional[str] = None
    task_progress: Optional[float] = None
    upstream_job_id: Optional[str] = None
    token_id: Optional[str] = None
    token_account_name: Optional[str] = None
    token_account_email: Optional[str] = None
    token_source: Optional[str] = None
    token_attempt: Optional[int] = None
    exception_class: Optional[str] = None
    traceback: Optional[str] = None


class ErrorDetailStore:
    def __init__(self, file_path: Path, max_items: int = 5000) -> None:
        self._file_path = file_path
        self._lock = threading.Lock()
        self._max_items = max(200, int(max_items or 5000))
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        if not self._file_path.exists():
            self._file_path.touch()

    def _truncate_to_max_locked(self) -> None:
        with self._file_path.open("r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) <= self._max_items:
            return
        kept = lines[-self._max_items :]
        with self._file_path.open("w", encoding="utf-8") as f:
            f.writelines(kept)

    def add(self, item: ErrorDetailRecord) -> None:
        payload = asdict(item)
        with self._lock:
            with self._file_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self._truncate_to_max_locked()

    def get(self, code: str) -> Optional[dict]:
        target = str(code or "").strip()
        if not target:
            return None
        with self._lock:
            with self._file_path.open("r", encoding="utf-8") as f:
                lines = f.readlines()

        for line in reversed(lines):
            raw = line.strip()
            if not raw:
                continue
            try:
                item = json.loads(raw)
            except Exception:
                continue
            if isinstance(item, dict) and str(item.get("code") or "") == target:
                return item
        return None


class LiveRequestStore:
    def __init__(self, max_items: int = 2000) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, dict] = {}
        self._max_items = max(100, int(max_items or 2000))

    def upsert(self, item_id: str, payload: dict) -> None:
        iid = str(item_id or "").strip()
        if not iid or not isinstance(payload, dict):
            return
        with self._lock:
            old = self._items.get(iid, {})
            merged = dict(old)
            merged.update(payload)
            merged["id"] = iid
            if not merged.get("ts"):
                merged["ts"] = time.time()
            self._items[iid] = merged
            if len(self._items) > self._max_items:
                pairs = sorted(
                    self._items.items(),
                    key=lambda x: float((x[1] or {}).get("ts") or 0),
                )
                overflow = len(self._items) - self._max_items
                for key, _ in pairs[:overflow]:
                    self._items.pop(key, None)

    def remove(self, item_id: str) -> None:
        iid = str(item_id or "").strip()
        if not iid:
            return
        with self._lock:
            self._items.pop(iid, None)

    def get(self, item_id: str) -> Optional[dict]:
        iid = str(item_id or "").strip()
        if not iid:
            return None
        with self._lock:
            item = self._items.get(iid)
            if not isinstance(item, dict):
                return None
            return dict(item)

    def list(self, limit: int = 200) -> list[dict]:
        safe_limit = min(max(int(limit or 200), 1), 1000)
        with self._lock:
            data = list(self._items.values())
        data.sort(key=lambda x: float((x or {}).get("ts") or 0), reverse=True)
        return data[:safe_limit]

    def count_in_progress(self) -> int:
        with self._lock:
            vals = list(self._items.values())
        total = 0
        for item in vals:
            status = str((item or {}).get("task_status") or "").upper()
            if status == "IN_PROGRESS":
                total += 1
        return total
