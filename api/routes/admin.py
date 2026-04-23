import copy
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import requests
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.responses import RedirectResponse

from api.schemas import (
    AdminLoginRequest,
    ConfigUpdateRequest,
    ExportSelectionRequest,
    ProxyTestRequest,
    RefreshCookieBatchImportRequest,
    RefreshCookieImportRequest,
    RefreshProfileEnabledRequest,
    TokenAddRequest,
    TokenAutoRefreshBatchRequest,
    TokenBatchAddRequest,
    TokenCreditsBatchRefreshRequest,
    TokenInvalidCheckRequest,
    TokenRefreshBatchRequest,
)
from core.proxy_utils import (
    build_requests_proxies,
    resolve_basic_proxy,
    resolve_resource_proxy,
    test_authorized_endpoint,
    test_proxy_endpoint,
)


logger = logging.getLogger("uvicorn.error")

_IMPORT_REFRESH_JOB_TTL_SECONDS = 1800
_IMPORT_REFRESH_JOB_LOCK = threading.Lock()
_IMPORT_REFRESH_JOBS: Dict[str, Dict[str, Any]] = {}
_IMPORT_REFRESH_JOB_EXECUTOR = ThreadPoolExecutor(max_workers=4)
_TOKEN_REFRESH_JOB_TTL_SECONDS = 1800
_TOKEN_REFRESH_JOB_LOCK = threading.Lock()
_TOKEN_REFRESH_JOBS: Dict[str, Dict[str, Any]] = {}
_TOKEN_REFRESH_JOB_EXECUTOR = ThreadPoolExecutor(max_workers=4)


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 3)


def _timing_value(payload: Any, key: str) -> float:
    if not isinstance(payload, dict):
        return 0.0
    try:
        return float(payload.get(key) or 0)
    except Exception:
        return 0.0


def _index_success_from_timing(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    return int(payload.get("token_value_index_size") or 0) > 0 and (
        "token_upsert_index_ms" in payload
        or "token_index_ms_sum" in payload
        or "token_index_ms_max" in payload
    )


def _cleanup_import_refresh_jobs(now_ts: Optional[float] = None) -> None:
    current = float(now_ts or time.time())
    expired_ids = []
    for job_id, job in _IMPORT_REFRESH_JOBS.items():
        completed_at = float(job.get("completed_at") or 0)
        if completed_at and (current - completed_at) > _IMPORT_REFRESH_JOB_TTL_SECONDS:
            expired_ids.append(job_id)
    for job_id in expired_ids:
        _IMPORT_REFRESH_JOBS.pop(job_id, None)


def _cleanup_token_refresh_jobs(now_ts: Optional[float] = None) -> None:
    current = float(now_ts or time.time())
    expired_ids = []
    for job_id, job in _TOKEN_REFRESH_JOBS.items():
        completed_at = float(job.get("completed_at") or 0)
        if completed_at and (current - completed_at) > _TOKEN_REFRESH_JOB_TTL_SECONDS:
            expired_ids.append(job_id)
    for job_id in expired_ids:
        _TOKEN_REFRESH_JOBS.pop(job_id, None)


def _create_import_refresh_job(
    *,
    total_requested: int,
    request_duplicate_count: int,
    request_dedupe_ms: float,
    imported_entries: List[Dict[str, Any]],
    failed_entries: List[Dict[str, Any]],
) -> str:
    now_ts = time.time()
    job_id = uuid.uuid4().hex[:12]
    job = {
        "id": job_id,
        "status": "queued",
        "created_at": now_ts,
        "started_at": None,
        "completed_at": None,
        "created_perf": time.perf_counter(),
        "completed_perf": None,
        "total_requested": int(total_requested or 0),
        "request_duplicate_count": int(request_duplicate_count or 0),
        "request_dedupe_ms": float(request_dedupe_ms or 0),
        "failed": copy.deepcopy(failed_entries),
        "items": [],
    }
    for entry in imported_entries:
        profile = (
            copy.deepcopy(entry.get("profile"))
            if isinstance(entry.get("profile"), dict)
            else {}
        )
        job["items"].append(
            {
                "index": int(entry.get("index") or 0),
                "profile": profile,
                "profile_id": str(profile.get("id") or "").strip(),
                "profile_name": str(profile.get("name") or "").strip(),
                "reused_existing_profile": bool(
                    profile.get("reused_existing_profile")
                ),
                "status": "queued",
                "refresh_result": None,
                "refresh_error": "",
                "profile_import_ms": float(entry.get("profile_import_ms") or 0),
                "refresh_call_ms": 0.0,
            }
        )
    with _IMPORT_REFRESH_JOB_LOCK:
        _cleanup_import_refresh_jobs(now_ts)
        _IMPORT_REFRESH_JOBS[job_id] = job
    return job_id


def _mark_import_refresh_job_started(job_id: str) -> None:
    with _IMPORT_REFRESH_JOB_LOCK:
        job = _IMPORT_REFRESH_JOBS.get(job_id)
        if not job:
            return
        if not job.get("started_at"):
            job["started_at"] = time.time()
        job["status"] = "running"


def _mark_import_refresh_job_item_running(job_id: str, index: int) -> None:
    with _IMPORT_REFRESH_JOB_LOCK:
        job = _IMPORT_REFRESH_JOBS.get(job_id)
        if not job:
            return
        items = job.get("items") or []
        if 0 <= index < len(items):
            items[index]["status"] = "running"


def _mark_import_refresh_job_item_result(
    job_id: str,
    index: int,
    *,
    refresh_result: Optional[Dict[str, Any]] = None,
    refresh_error: str = "",
    refresh_call_ms: float = 0.0,
) -> None:
    with _IMPORT_REFRESH_JOB_LOCK:
        job = _IMPORT_REFRESH_JOBS.get(job_id)
        if not job:
            return
        items = job.get("items") or []
        if not (0 <= index < len(items)):
            return
        item = items[index]
        item["refresh_call_ms"] = float(refresh_call_ms or 0)
        if isinstance(refresh_result, dict):
            item["refresh_result"] = copy.deepcopy(refresh_result)
            item["refresh_error"] = ""
            item["status"] = "succeeded"
            return
        item["refresh_result"] = None
        item["refresh_error"] = str(refresh_error or "").strip()
        item["status"] = "failed"


def _mark_import_refresh_job_completed(job_id: str) -> None:
    with _IMPORT_REFRESH_JOB_LOCK:
        job = _IMPORT_REFRESH_JOBS.get(job_id)
        if not job:
            return
        job["status"] = "completed"
        job["completed_at"] = time.time()
        job["completed_perf"] = time.perf_counter()


def _build_import_refresh_payload(job_id: str) -> Optional[Dict[str, Any]]:
    with _IMPORT_REFRESH_JOB_LOCK:
        _cleanup_import_refresh_jobs()
        raw_job = _IMPORT_REFRESH_JOBS.get(job_id)
        if raw_job is None:
            return None
        job = copy.deepcopy(raw_job)

    items = job.get("items") or []
    failed = job.get("failed") or []
    refreshed = []
    refresh_failed = []
    profile_import_ms_sum = 0.0
    refresh_call_ms_sum = 0.0
    prepare_ms_sum = 0.0
    adobe_refresh_ms_sum = 0.0
    response_parse_ms_sum = 0.0
    account_ms_sum = 0.0
    token_upsert_ms_sum = 0.0
    token_index_ms_sum = 0.0
    token_index_ms_max = 0.0
    token_upsert_total_ms_sum = 0.0
    credits_ms_sum = 0.0
    token_value_index_size = 0
    token_profile_index_size = 0
    list_duplicate_count = 0
    queued_count = 0
    running_count = 0
    item_rows = []

    for item in items:
        profile_import_ms_sum += _timing_value(item, "profile_import_ms")
        refresh_call_ms_sum += _timing_value(item, "refresh_call_ms")
        item_status = str(item.get("status") or "").strip().lower()
        if item_status == "queued":
            queued_count += 1
        elif item_status == "running":
            running_count += 1

        row = {
            "index": int(item.get("index") or 0),
            "profile_id": str(item.get("profile_id") or "").strip(),
            "profile_name": str(item.get("profile_name") or "").strip(),
            "status": item_status or "queued",
            "detail": str(item.get("refresh_error") or "").strip(),
            "profile_import_ms": _timing_value(item, "profile_import_ms"),
            "refresh_call_ms": _timing_value(item, "refresh_call_ms"),
        }

        refresh_result = item.get("refresh_result") or {}
        if isinstance(refresh_result, dict) and refresh_result:
            row["result"] = refresh_result
            refreshed_item = {
                "index": item.get("index"),
                "profile_id": item.get("profile_id"),
                "profile_name": item.get("profile_name"),
                "result": refresh_result,
            }
            refreshed.append(refreshed_item)
            if bool(item.get("reused_existing_profile")) or bool(
                refresh_result.get("token_duplicate")
            ):
                list_duplicate_count += 1
            timing = refresh_result.get("timing") or {}
            prepare_ms_sum += _timing_value(timing, "prepare_ms")
            adobe_refresh_ms_sum += _timing_value(timing, "adobe_refresh_ms")
            response_parse_ms_sum += _timing_value(timing, "response_parse_ms")
            account_ms_sum += _timing_value(timing, "account_ms")
            token_upsert_ms_sum += _timing_value(timing, "token_upsert_ms")
            token_index_ms = _timing_value(timing, "token_upsert_index_ms")
            token_index_ms_sum += token_index_ms
            token_index_ms_max = max(token_index_ms_max, token_index_ms)
            token_upsert_total_ms_sum += _timing_value(
                timing, "token_upsert_total_ms"
            )
            credits_ms_sum += _timing_value(timing, "credits_ms")
            if isinstance(timing, dict):
                token_value_index_size = max(
                    token_value_index_size,
                    int(timing.get("token_value_index_size") or 0),
                )
                token_profile_index_size = max(
                    token_profile_index_size,
                    int(timing.get("token_profile_index_size") or 0),
                )

        if item_status == "failed":
            refresh_failed.append(
                {
                    "index": item.get("index"),
                    "profile_id": item.get("profile_id"),
                    "profile_name": item.get("profile_name"),
                    "detail": str(item.get("refresh_error") or "").strip(),
                }
            )

        item_rows.append(row)

    completed_count = len(refreshed) + len(refresh_failed)
    pending_count = queued_count + running_count
    success_count = max(0, len(refreshed) - list_duplicate_count)
    duplicate_count = int(job.get("request_duplicate_count") or 0) + list_duplicate_count
    error_count = len(failed) + len(refresh_failed)
    imported_profiles = [
        copy.deepcopy(item.get("profile"))
        for item in items
        if isinstance(item.get("profile"), dict)
    ]
    job_completed = pending_count == 0 and (
        str(job.get("status") or "").strip().lower() == "completed"
    )
    job_status = "running"
    if job_completed:
        if not failed and not refresh_failed:
            job_status = "ok"
        elif refreshed:
            job_status = "partial"
        else:
            job_status = "failed"
    elif completed_count == 0:
        job_status = "queued"

    end_perf = job.get("completed_perf") if job_completed else time.perf_counter()
    total_ms = round(
        max(0.0, float(end_perf or 0) - float(job.get("created_perf") or 0)) * 1000,
        3,
    )
    response_timing = {
        "request_dedupe_ms": _timing_value(job, "request_dedupe_ms"),
        "profile_import_ms_sum": profile_import_ms_sum,
        "refresh_call_ms_sum": refresh_call_ms_sum,
        "prepare_ms_sum": prepare_ms_sum,
        "adobe_refresh_ms_sum": adobe_refresh_ms_sum,
        "response_parse_ms_sum": response_parse_ms_sum,
        "account_ms_sum": account_ms_sum,
        "token_upsert_ms_sum": token_upsert_ms_sum,
        "token_index_ms_sum": token_index_ms_sum,
        "token_index_ms_max": token_index_ms_max,
        "token_upsert_total_ms_sum": token_upsert_total_ms_sum,
        "credits_ms_sum": credits_ms_sum,
        "total_ms": total_ms,
        "token_value_index_size": token_value_index_size,
        "token_profile_index_size": token_profile_index_size,
    }
    response_timing["token_index_success"] = _index_success_from_timing(
        response_timing
    )
    return {
        "status": job_status,
        "total": int(job.get("total_requested") or 0),
        "processed_count": len(items) + len(failed),
        "deduplicated_count": int(job.get("request_duplicate_count") or 0),
        "success_count": success_count,
        "duplicate_count": duplicate_count,
        "error_count": error_count,
        "request_duplicate_count": int(job.get("request_duplicate_count") or 0),
        "list_duplicate_count": list_duplicate_count,
        "overwritten_count": list_duplicate_count,
        "imported_count": len(imported_profiles),
        "failed_count": len(failed),
        "refreshed_count": len(refreshed),
        "refresh_failed_count": len(refresh_failed),
        "profiles": imported_profiles,
        "failed": failed,
        "refreshed": refreshed,
        "refresh_failed": refresh_failed,
        "items": item_rows,
        "timing": response_timing,
        "background_refresh": {
            "job_id": str(job.get("id") or "").strip(),
            "status": str(job.get("status") or "").strip().lower(),
            "total_count": len(items),
            "queued_count": queued_count,
            "running_count": running_count,
            "completed_count": completed_count,
            "pending_count": pending_count,
            "completed": job_completed,
        },
    }


def _run_import_refresh_job(
    job_id: str,
    *,
    refresh_manager: Any,
    concurrency: int,
) -> None:
    payload = _build_import_refresh_payload(job_id)
    if not payload:
        return
    background = payload.get("background_refresh") or {}
    total_count = int(background.get("total_count") or 0)
    if total_count <= 0:
        _mark_import_refresh_job_completed(job_id)
        return

    _mark_import_refresh_job_started(job_id)
    max_workers = min(max(1, int(concurrency or 1)), total_count)
    logger.info(
        "import_cookie_background_refresh_started job_id=%s total=%s concurrency=%s",
        job_id,
        total_count,
        max_workers,
    )

    with _IMPORT_REFRESH_JOB_LOCK:
        job = _IMPORT_REFRESH_JOBS.get(job_id) or {}
        items = copy.deepcopy(job.get("items") or [])

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {}
        for index, item in enumerate(items):
            profile_id = str(item.get("profile_id") or "").strip()
            if not profile_id:
                _mark_import_refresh_job_item_result(
                    job_id,
                    index,
                    refresh_error="missing profile id",
                )
                continue
            _mark_import_refresh_job_item_running(job_id, index)
            refresh_started = time.perf_counter()
            future = executor.submit(refresh_manager.refresh_once, profile_id)
            future_map[future] = (index, refresh_started)

        for future in as_completed(future_map):
            index, refresh_started = future_map[future]
            refresh_call_ms = _elapsed_ms(refresh_started)
            try:
                refresh_result = future.result()
            except Exception as exc:
                _mark_import_refresh_job_item_result(
                    job_id,
                    index,
                    refresh_error=str(exc),
                    refresh_call_ms=refresh_call_ms,
                )
                continue
            _mark_import_refresh_job_item_result(
                job_id,
                index,
                refresh_result=refresh_result,
                refresh_call_ms=refresh_call_ms,
            )

    _mark_import_refresh_job_completed(job_id)
    summary = _build_import_refresh_payload(job_id) or {}
    logger.info(
        "import_cookie_background_refresh_completed job_id=%s status=%s success=%s "
        "failed=%s duplicate=%s refreshed=%s refresh_failed=%s total_ms=%.3f",
        job_id,
        summary.get("status"),
        summary.get("success_count"),
        summary.get("error_count"),
        summary.get("duplicate_count"),
        summary.get("refreshed_count"),
        summary.get("refresh_failed_count"),
        _timing_value(summary.get("timing"), "total_ms"),
    )


def _create_token_refresh_job(
    *,
    token_entries: List[Dict[str, Any]],
) -> str:
    now_ts = time.time()
    job_id = uuid.uuid4().hex[:12]
    job = {
        "id": job_id,
        "status": "queued",
        "created_at": now_ts,
        "started_at": None,
        "completed_at": None,
        "created_perf": time.perf_counter(),
        "completed_perf": None,
        "items": [
            {
                "index": int(entry.get("index") or 0),
                "token_id": str(entry.get("token_id") or "").strip(),
                "token_account_name": str(entry.get("token_account_name") or "").strip(),
                "token_account_email": str(
                    entry.get("token_account_email") or ""
                ).strip(),
                "status": "queued",
                "detail": "",
                "refresh_result": None,
                "refresh_call_ms": 0.0,
            }
            for entry in token_entries
        ],
    }
    with _TOKEN_REFRESH_JOB_LOCK:
        _cleanup_token_refresh_jobs(now_ts)
        _TOKEN_REFRESH_JOBS[job_id] = job
    return job_id


def _mark_token_refresh_job_started(job_id: str) -> None:
    with _TOKEN_REFRESH_JOB_LOCK:
        job = _TOKEN_REFRESH_JOBS.get(job_id)
        if not job:
            return
        if not job.get("started_at"):
            job["started_at"] = time.time()
        job["status"] = "running"


def _mark_token_refresh_job_item_running(job_id: str, index: int) -> None:
    with _TOKEN_REFRESH_JOB_LOCK:
        job = _TOKEN_REFRESH_JOBS.get(job_id)
        if not job:
            return
        items = job.get("items") or []
        if 0 <= index < len(items):
            items[index]["status"] = "running"


def _mark_token_refresh_job_item_result(
    job_id: str,
    index: int,
    *,
    status: str,
    detail: str = "",
    refresh_result: Optional[Dict[str, Any]] = None,
    refresh_call_ms: float = 0.0,
) -> None:
    with _TOKEN_REFRESH_JOB_LOCK:
        job = _TOKEN_REFRESH_JOBS.get(job_id)
        if not job:
            return
        items = job.get("items") or []
        if not (0 <= index < len(items)):
            return
        item = items[index]
        item["status"] = str(status or "").strip().lower() or "failed"
        item["detail"] = str(detail or "").strip()
        item["refresh_call_ms"] = float(refresh_call_ms or 0.0)
        item["refresh_result"] = (
            copy.deepcopy(refresh_result)
            if isinstance(refresh_result, dict)
            else None
        )


def _mark_token_refresh_job_completed(job_id: str) -> None:
    with _TOKEN_REFRESH_JOB_LOCK:
        job = _TOKEN_REFRESH_JOBS.get(job_id)
        if not job:
            return
        job["status"] = "completed"
        job["completed_at"] = time.time()
        job["completed_perf"] = time.perf_counter()


def _build_token_refresh_job_payload(job_id: str) -> Optional[Dict[str, Any]]:
    with _TOKEN_REFRESH_JOB_LOCK:
        _cleanup_token_refresh_jobs()
        raw_job = _TOKEN_REFRESH_JOBS.get(job_id)
        if raw_job is None:
            return None
        job = copy.deepcopy(raw_job)

    items = job.get("items") or []
    refreshed = []
    skipped = []
    failed = []
    queued_count = 0
    running_count = 0
    refresh_call_ms_sum = 0.0
    item_rows = []

    for item in items:
        item_status = str(item.get("status") or "").strip().lower()
        refresh_call_ms_sum += _timing_value(item, "refresh_call_ms")
        if item_status == "queued":
            queued_count += 1
        elif item_status == "running":
            running_count += 1

        row = {
            "index": int(item.get("index") or 0),
            "token_id": str(item.get("token_id") or "").strip(),
            "token_account_name": str(item.get("token_account_name") or "").strip(),
            "token_account_email": str(item.get("token_account_email") or "").strip(),
            "status": item_status or "queued",
            "detail": str(item.get("detail") or "").strip(),
            "refresh_call_ms": _timing_value(item, "refresh_call_ms"),
        }
        refresh_result = item.get("refresh_result") or {}
        if isinstance(refresh_result, dict) and refresh_result:
            row["result"] = refresh_result
        item_rows.append(row)

        if item_status == "succeeded":
            refreshed.append(row)
        elif item_status == "skipped":
            skipped.append(row)
        elif item_status == "failed":
            failed.append(row)

    completed_count = len(refreshed) + len(skipped) + len(failed)
    pending_count = queued_count + running_count
    job_completed = pending_count == 0 and (
        str(job.get("status") or "").strip().lower() == "completed"
    )
    job_status = "running"
    if job_completed:
        if failed:
            job_status = "partial" if refreshed else "failed"
        else:
            job_status = "ok"
    elif completed_count == 0:
        job_status = "queued"

    end_perf = job.get("completed_perf") if job_completed else time.perf_counter()
    total_ms = round(
        max(0.0, float(end_perf or 0) - float(job.get("created_perf") or 0)) * 1000,
        3,
    )

    return {
        "status": job_status,
        "total": len(items),
        "success_count": len(refreshed),
        "refreshed_count": len(refreshed),
        "skipped_count": len(skipped),
        "failed_count": len(failed),
        "refreshed": refreshed,
        "skipped": skipped,
        "failed": failed,
        "items": item_rows,
        "timing": {
            "refresh_call_ms_sum": refresh_call_ms_sum,
            "total_ms": total_ms,
        },
        "background_refresh": {
            "job_id": str(job.get("id") or "").strip(),
            "status": str(job.get("status") or "").strip().lower(),
            "total_count": len(items),
            "queued_count": queued_count,
            "running_count": running_count,
            "completed_count": completed_count,
            "pending_count": pending_count,
            "completed": job_completed,
        },
    }


def _run_token_refresh_job(
    job_id: str,
    *,
    token_manager: Any,
    refresh_manager: Any,
    concurrency: int,
) -> None:
    payload = _build_token_refresh_job_payload(job_id)
    if not payload:
        return
    background = payload.get("background_refresh") or {}
    total_count = int(background.get("total_count") or 0)
    if total_count <= 0:
        _mark_token_refresh_job_completed(job_id)
        return

    _mark_token_refresh_job_started(job_id)
    max_workers = min(max(1, int(concurrency or 1)), total_count)
    logger.info(
        "token_refresh_batch_started job_id=%s total=%s concurrency=%s",
        job_id,
        total_count,
        max_workers,
    )

    with _TOKEN_REFRESH_JOB_LOCK:
        job = _TOKEN_REFRESH_JOBS.get(job_id) or {}
        items = copy.deepcopy(job.get("items") or [])

    def refresh_one(index: int, item: Dict[str, Any]):
        tid = str(item.get("token_id") or "").strip()
        token_info = token_manager.get_by_id(tid)
        if not token_info:
            return index, "failed", {"detail": "token not found"}

        profile_id = str(token_info.get("refresh_profile_id") or "").strip()
        if not profile_id:
            return index, "skipped", {
                "detail": "this token is not bound to an auto refresh profile"
            }

        refresh_started = time.perf_counter()
        try:
            refresh_result = refresh_manager.refresh_once(profile_id)
        except Exception as exc:
            return index, "failed", {
                "detail": str(exc),
                "refresh_call_ms": _elapsed_ms(refresh_started),
            }
        return index, "succeeded", {
            "refresh_result": refresh_result,
            "refresh_call_ms": _elapsed_ms(refresh_started),
        }

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {}
        for index, item in enumerate(items):
            _mark_token_refresh_job_item_running(job_id, index)
            future = executor.submit(refresh_one, index, item)
            future_map[future] = index

        for future in as_completed(future_map):
            index = future_map[future]
            try:
                _, status, payload = future.result()
            except Exception as exc:
                _mark_token_refresh_job_item_result(
                    job_id,
                    index,
                    status="failed",
                    detail=str(exc),
                )
                continue
            _mark_token_refresh_job_item_result(
                job_id,
                index,
                status=status,
                detail=str(payload.get("detail") or "").strip(),
                refresh_result=payload.get("refresh_result"),
                refresh_call_ms=_timing_value(payload, "refresh_call_ms"),
            )

    _mark_token_refresh_job_completed(job_id)
    summary = _build_token_refresh_job_payload(job_id) or {}
    logger.info(
        "token_refresh_batch_completed job_id=%s status=%s success=%s skipped=%s "
        "failed=%s total_ms=%.3f",
        job_id,
        summary.get("status"),
        summary.get("success_count"),
        summary.get("skipped_count"),
        summary.get("failed_count"),
        _timing_value(summary.get("timing"), "total_ms"),
    )


def build_admin_router(
    *,
    static_dir: Path,
    token_manager,
    config_manager,
    refresh_manager,
    log_store,
    error_store,
    live_log_store,
    require_admin_auth: Callable[[Request], None],
    is_admin_authenticated: Callable[[Request], bool],
    apply_client_config: Callable[[], None],
    get_generated_storage_stats: Callable[[], dict[str, Any]],
    get_redis_health: Callable[[], dict[str, Any]],
) -> APIRouter:
    router = APIRouter()

    def get_batch_concurrency() -> int:
        try:
            value = int(config_manager.get("batch_concurrency", 5) or 5)
        except Exception:
            value = 5
        return max(1, min(100, value))

    def delete_token_and_linked_profile(token_id: str) -> bool:
        token_info = token_manager.get_by_id(token_id)
        if not token_info:
            return False

        profile_id = str(token_info.get("refresh_profile_id") or "").strip()
        if token_info.get("auto_refresh") and profile_id:
            try:
                refresh_manager.remove_profile(profile_id)
            except KeyError:
                token_manager.remove(token_id)
        else:
            token_manager.remove(token_id)
        return True

    def disable_auto_refresh_for_token_info(token_info: dict) -> tuple[bool, str]:
        if not isinstance(token_info, dict) or not bool(token_info.get("auto_refresh")):
            return False, ""
        profile_id = str(token_info.get("refresh_profile_id") or "").strip()
        if not profile_id:
            return False, ""
        refresh_manager.set_enabled(profile_id, False)
        return True, profile_id

    def _mark_token_abnormal_for_check(
        token_info: dict,
        *,
        detail: str,
    ) -> dict:
        token_id = str(token_info.get("id") or "").strip()
        previous_status = str(token_info.get("status") or "").strip().lower() or "active"
        final_status = previous_status
        status_changed = False
        if token_id and previous_status not in {"invalid", "exhausted", "abnormal"}:
            updated = token_manager.report_abnormal_by_identity(token_id=token_id)
            refreshed = (
                updated
                if isinstance(updated, dict) and updated
                else token_manager.get_by_id(token_id) or {}
            )
            if isinstance(refreshed, dict) and refreshed:
                token_info = refreshed
            final_status = "abnormal"
            status_changed = previous_status != "abnormal"

        profile_disabled = False
        disabled_profile_id = ""
        auto_refresh_disable_error = ""
        try:
            profile_disabled, disabled_profile_id = disable_auto_refresh_for_token_info(
                token_info
            )
        except Exception as exc:
            auto_refresh_disable_error = str(exc)

        payload = {
            "token_id": token_id,
            "result": "abnormal",
            "detail": str(detail or "").strip() or "token marked as abnormal",
            "previous_status": previous_status,
            "status": final_status,
            "status_changed": status_changed,
            "auto_refresh_disabled": profile_disabled,
            "disabled_profile_id": disabled_profile_id or None,
        }
        if auto_refresh_disable_error:
            payload["auto_refresh_disable_error"] = auto_refresh_disable_error
        return payload

    def _response_text_for_invalid_check(resp) -> str:
        parts = [str(getattr(resp, "text", "") or "")]
        try:
            payload = resp.json()
        except Exception:
            payload = None

        def collect(value: Any):
            if isinstance(value, dict):
                for child in value.values():
                    collect(child)
            elif isinstance(value, list):
                for child in value:
                    collect(child)
            elif value is not None:
                parts.append(str(value))

        collect(payload)
        return "\n".join(parts).casefold()

    def _check_token_invalid_or_expired(token_info: dict) -> dict:
        token_id = str(token_info.get("id") or "").strip()
        token_value = str(token_info.get("value") or "").strip()
        if not token_value:
            return {
                "token_id": token_id,
                "result": "abnormal",
                "detail": "empty token",
            }

        account_id = ""
        try:
            account_id = str(
                refresh_manager._extract_account_id(token_value) or ""
            ).strip()
        except Exception:
            account_id = ""
        if not account_id:
            return {
                "token_id": token_id,
                "result": "abnormal",
                "detail": "account_id not found in token",
            }

        cfg = config_manager.get_all()
        proxy = resolve_basic_proxy(cfg)
        resp = requests.get(
            "https://firefly.adobe.io/v1/credits/balance",
            headers={
                "Authorization": f"Bearer {token_value}",
                "x-api-key": "SunbreakWebUI1",
                "x-account-id": account_id,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=20,
            proxies=build_requests_proxies(proxy),
        )
        status_code = int(resp.status_code)
        text = _response_text_for_invalid_check(resp)
        if "token invalid or expired" in text:
            return {
                "token_id": token_id,
                "status_code": status_code,
                "result": "invalid",
                "detail": "Token invalid or expired",
            }
        if status_code == 200:
            return {
                "token_id": token_id,
                "status_code": status_code,
                "result": "valid",
                "detail": "authorized request succeeded",
            }
        if status_code == 403:
            return {
                "token_id": token_id,
                "status_code": status_code,
                "result": "abnormal",
                "detail": "credits endpoint returned 403",
            }
        return {
            "token_id": token_id,
            "status_code": status_code,
            "result": "unknown",
            "detail": f"response did not contain Token invalid or expired ({status_code})",
        }

    def build_basic_business_proxy_result(proxy: str) -> dict[str, Any]:
        result = {
            "name": "basic_business",
            "enabled": bool(proxy),
            "ok": False,
            "target_url": "https://firefly.adobe.io/v1/credits/balance",
            "proxy": proxy,
            "elapsed_ms": 0,
            "status_code": None,
            "message": "",
            "token_id": "",
            "token_source": "",
            "token_preview": "",
            "account_id": "",
        }
        if not proxy:
            result["message"] = "basic proxy disabled"
            return result

        active_ids = []
        try:
            active_ids = token_manager.list_active_ids()
        except Exception:
            active_ids = []
        token_info = None
        for token_id in active_ids:
            token_info = token_manager.get_by_id(token_id)
            if token_info and str(token_info.get("value") or "").strip():
                break
        if not token_info:
            result["message"] = "no active token available for business auth test"
            return result

        token_value = str(token_info.get("value") or "").strip()
        token_id = str(token_info.get("id") or "").strip()
        token_source = str(token_info.get("source") or "manual").strip()
        token_preview = (
            token_value[:10] + "..." + token_value[-6:]
            if len(token_value) > 20
            else "***"
        )
        account_id = ""
        try:
            account_id = str(refresh_manager._extract_account_id(token_value) or "").strip()
        except Exception:
            account_id = ""

        result.update(
            {
                "token_id": token_id,
                "token_source": token_source,
                "token_preview": token_preview,
                "account_id": account_id,
            }
        )
        if not account_id:
            result["message"] = "active token found, but account_id could not be extracted"
            return result

        auth_result = test_authorized_endpoint(
            check_name="basic_business",
            proxy=proxy,
            target_url="https://firefly.adobe.io/v1/credits/balance",
            headers={
                "Authorization": f"Bearer {token_value}",
                "x-api-key": "SunbreakWebUI1",
                "x-account-id": account_id,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        auth_result.update(
            {
                "token_id": token_id,
                "token_source": token_source,
                "token_preview": token_preview,
                "account_id": account_id,
            }
        )
        return auth_result

    @router.get("/api/v1/health")
    def health():
        return {
            "status": "ok",
            "pool_size": token_manager.count(),
            "redis": get_redis_health(),
        }

    @router.get("/api/v1/health/redis")
    def health_redis():
        return get_redis_health()

    @router.post("/api/v1/proxy/test")
    def test_proxy(req: ProxyTestRequest, request: Request):
        require_admin_auth(request)
        cfg = config_manager.get_all()
        incoming = req.model_dump(exclude_unset=True)
        cfg.update(incoming)
        basic_proxy = resolve_basic_proxy(cfg)
        resource_proxy = resolve_resource_proxy(cfg)
        basic_result = test_proxy_endpoint(
            proxy_label="basic",
            proxy=basic_proxy,
            target_url="https://firefly.adobe.io/v1/credits/balance",
        )
        resource_result = test_proxy_endpoint(
            proxy_label="resource",
            proxy=resource_proxy,
            target_url="https://firefly-3p.ff.adobe.io/v2/storage/image",
        )
        basic_business_result = build_basic_business_proxy_result(basic_proxy)
        return {
            "status": "ok",
            "connectivity": {
                "basic": basic_result,
                "resource": resource_result,
            },
            "business": {
                "basic": basic_business_result,
            },
        }

    @router.get("/login", include_in_schema=False)
    def page_login(request: Request):
        if is_admin_authenticated(request):
            return RedirectResponse(url="/")
        return FileResponse(static_dir / "login.html")

    @router.post("/api/v1/auth/login")
    def admin_login(req: AdminLoginRequest, request: Request):
        username = str(req.username or "").strip()
        password = str(req.password or "")
        expected_username = str(
            config_manager.get("admin_username", "admin") or "admin"
        ).strip()
        expected_password = str(
            config_manager.get("admin_password", "admin") or "admin"
        )

        if username != expected_username or password != expected_password:
            raise HTTPException(status_code=401, detail="Invalid username or password")

        request.session.clear()
        request.session["admin_auth"] = True
        request.session["username"] = username
        request.session["login_at"] = int(time.time())
        return {"status": "ok", "username": username}

    @router.get("/api/v1/auth/me")
    def admin_me(request: Request):
        if not is_admin_authenticated(request):
            raise HTTPException(status_code=401, detail="Unauthorized")
        return {
            "authenticated": True,
            "username": str((request.session or {}).get("username") or ""),
        }

    @router.post("/api/v1/auth/logout")
    def admin_logout(request: Request):
        request.session.clear()
        return {"status": "ok"}

    @router.get("/", include_in_schema=False)
    def page_root(request: Request):
        if not is_admin_authenticated(request):
            return RedirectResponse(url="/login")
        return FileResponse(static_dir / "admin.html")

    @router.get("/api/v1/logs")
    def list_logs(
        request: Request,
        limit: int = 20,
        page: int = 1,
        failed_only: bool = False,
        account: str = "",
        media_kind: str = "",
    ):
        require_admin_auth(request)
        logs, total = log_store.list(
            limit=limit,
            page=page,
            failed_only=bool(failed_only),
            account=str(account or "").strip(),
            media_kind=str(media_kind or "").strip().lower(),
        )
        safe_limit = min(max(int(limit or 20), 1), 100)
        safe_page = max(int(page or 1), 1)
        total_pages = (total + safe_limit - 1) // safe_limit if total > 0 else 1
        if safe_page > total_pages:
            safe_page = total_pages
        return {
            "logs": logs,
            "page": safe_page,
            "limit": safe_limit,
            "total": total,
            "total_pages": total_pages,
            "filters": {
                "failed_only": bool(failed_only),
                "account": str(account or "").strip(),
                "media_kind": str(media_kind or "").strip().lower(),
            },
        }

    @router.get("/api/v1/logs/failed-accounts")
    def list_failed_accounts(request: Request, limit: int = 200):
        require_admin_auth(request)
        items = log_store.list_failed_accounts(limit=limit)
        return {"items": items, "total": len(items)}

    @router.get("/api/v1/logs/errors/{code}")
    def get_error_detail(code: str, request: Request):
        require_admin_auth(request)
        item = error_store.get(code)
        if not item:
            raise HTTPException(status_code=404, detail="error code not found")
        return item

    @router.get("/api/v1/logs/running")
    def list_running_logs(request: Request, limit: int = 200):
        require_admin_auth(request)
        rows = live_log_store.list(limit=limit)
        items = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            status = str(item.get("task_status") or "").upper()
            if status != "IN_PROGRESS":
                continue
            items.append(item)
        return {"items": items, "total": len(items)}

    def _resolve_logs_stats_range(range_key: str) -> tuple[str, float, float]:
        now_dt = datetime.now()
        now_ts = time.time()
        key = str(range_key or "today").strip().lower()
        if key == "today":
            start_dt = datetime(now_dt.year, now_dt.month, now_dt.day)
            end_ts = now_ts
        elif key == "yesterday":
            today_start = datetime(now_dt.year, now_dt.month, now_dt.day)
            start_dt = today_start - timedelta(days=1)
            end_ts = today_start.timestamp()
        elif key == "3d":
            start_dt = now_dt - timedelta(days=3)
            end_ts = now_ts
        elif key == "7d":
            start_dt = now_dt - timedelta(days=7)
            end_ts = now_ts
        elif key == "30d":
            start_dt = now_dt - timedelta(days=30)
            end_ts = now_ts
        else:
            raise HTTPException(
                status_code=400,
                detail="range must be one of: today, yesterday, 3d, 7d, 30d",
            )
        return key, start_dt.timestamp(), end_ts

    @router.get("/api/v1/logs/stats")
    def logs_stats(request: Request, range: str = "today"):
        require_admin_auth(request)
        range_key, start_ts, end_ts = _resolve_logs_stats_range(range)
        payload = log_store.stats(start_ts=start_ts, end_ts=end_ts)
        payload["in_progress_requests"] = live_log_store.count_in_progress()
        payload.update({"range": range_key, "start_ts": start_ts, "end_ts": end_ts})
        return payload

    @router.delete("/api/v1/logs")
    def clear_logs(request: Request):
        require_admin_auth(request)
        log_store.clear()
        return {"status": "ok"}

    @router.get("/api/v1/tokens")
    def list_tokens(
        request: Request,
        page: int = 1,
        page_size: int = 50,
        status: str = "",
        credits: str = "",
    ):
        require_admin_auth(request)
        payload = token_manager.list_page(
            page=page,
            page_size=page_size,
            status=status,
            credits=credits,
        )
        tokens = payload.get("tokens") or []
        for item in tokens:
            if not bool(item.get("auto_refresh")):
                item["auto_refresh_enabled"] = None
                continue
            pid = str(item.get("refresh_profile_id") or "").strip()
            if str(item.get("status") or "").strip().lower() in {"exhausted", "invalid", "abnormal"} and pid:
                try:
                    refresh_manager.set_enabled(pid, False)
                except Exception:
                    pass
            item["auto_refresh_enabled"] = refresh_manager.is_profile_enabled(pid)
        return {
            "tokens": tokens,
            "summary": payload.get("summary") or {},
            "pagination": payload.get("pagination") or {},
        }

    @router.post("/api/v1/tokens")
    def add_token(req: TokenAddRequest, request: Request):
        require_admin_auth(request)
        if not req.token.strip():
            raise HTTPException(status_code=400, detail="Empty token")
        existing_duplicate = token_manager.has_value(req.token)
        result = token_manager.add(req.token)
        duplicate_count = 1 if bool(result.get("_duplicate")) else 0
        list_duplicate_count = 1 if duplicate_count and existing_duplicate else 0
        request_duplicate_count = 1 if duplicate_count and not existing_duplicate else 0
        success_count = 0 if duplicate_count else 1
        return {
            "status": "ok",
            "success_count": success_count,
            "failed_count": 0,
            "duplicate_count": duplicate_count,
            "request_duplicate_count": request_duplicate_count,
            "list_duplicate_count": list_duplicate_count,
            "overwritten_count": list_duplicate_count,
        }

    @router.post("/api/v1/tokens/batch")
    def add_tokens_batch(req: TokenBatchAddRequest, request: Request):
        require_admin_auth(request)
        if not req.tokens:
            raise HTTPException(status_code=400, detail="tokens is required")

        seen_in_request = set()
        existing_by_token = {}
        success_count = 0
        failed_count = 0
        duplicate_count = 0
        request_duplicate_count = 0
        list_duplicate_count = 0
        for raw in req.tokens:
            token = str(raw or "").strip()
            if not token:
                failed_count += 1
                continue
            normalized_token = token_manager._normalize_token_value(token)  # type: ignore[attr-defined]
            in_current_request = normalized_token in seen_in_request
            if normalized_token not in existing_by_token:
                existing_by_token[normalized_token] = token_manager.has_value(
                    normalized_token
                )
            in_existing_list = bool(existing_by_token.get(normalized_token))
            result = token_manager.add(token)
            if bool(result.get("_duplicate")):
                duplicate_count += 1
                if in_existing_list:
                    list_duplicate_count += 1
                elif in_current_request:
                    request_duplicate_count += 1
            else:
                success_count += 1
            seen_in_request.add(normalized_token)

        if success_count == 0 and duplicate_count == 0:
            raise HTTPException(status_code=400, detail="no valid token provided")

        return {
            "status": "ok" if failed_count == 0 else "partial",
            "total": len(req.tokens),
            "success_count": success_count,
            "failed_count": failed_count,
            "duplicate_count": duplicate_count,
            "request_duplicate_count": request_duplicate_count,
            "list_duplicate_count": list_duplicate_count,
            "overwritten_count": list_duplicate_count,
            "added_count": success_count,
        }

    @router.post("/api/v1/tokens/export")
    def export_tokens(req: ExportSelectionRequest, request: Request):
        require_admin_auth(request)
        token_ids = req.ids if isinstance(req.ids, list) else None
        exported = token_manager.export_tokens(token_ids)
        return {
            "status": "ok",
            "total": len(exported),
            "selected": bool(token_ids),
            "tokens": exported,
        }

    @router.post("/api/v1/tokens/delete-batch")
    def delete_tokens_batch(req: ExportSelectionRequest, request: Request):
        require_admin_auth(request)
        token_ids = req.ids if isinstance(req.ids, list) else None
        normalized_ids = [
            str(x or "").strip() for x in (token_ids or []) if str(x or "").strip()
        ]
        if not normalized_ids:
            raise HTTPException(status_code=400, detail="ids is required")

        deleted = []
        missing = []
        for tid in normalized_ids:
            if delete_token_and_linked_profile(tid):
                deleted.append(tid)
            else:
                missing.append(tid)

        if not deleted:
            raise HTTPException(status_code=404, detail="no token deleted")

        return {
            "status": "ok" if not missing else "partial",
            "deleted_count": len(deleted),
            "missing_count": len(missing),
            "deleted_ids": deleted,
            "missing_ids": missing,
        }

    @router.post("/api/v1/tokens/check-invalid-batch")
    def check_invalid_tokens_batch(req: TokenInvalidCheckRequest, request: Request):
        require_admin_auth(request)
        token_ids = [
            str(x or "").strip() for x in (req.ids or []) if str(x or "").strip()
        ]
        if not token_ids:
            raise HTTPException(status_code=400, detail="ids is required")

        checked = []
        invalid = []
        valid = []
        abnormal = []
        skipped = []
        failed = []
        disabled_profile_ids = []
        disabled_tokens = []
        max_workers = min(get_batch_concurrency(), len(token_ids))

        def check_one(index: int, tid: str):
            token_info = token_manager.get_by_id(tid)
            if not token_info:
                return index, "skipped", {"token_id": tid, "detail": "token not found"}

            current_status = str(token_info.get("status") or "").strip().lower()
            if current_status in {"invalid", "exhausted", "abnormal"}:
                return index, "skipped", {
                    "token_id": tid,
                    "status": current_status,
                    "detail": f"token already in terminal status: {current_status}",
                }
            if current_status == "disabled":
                return index, "skipped", {
                    "token_id": tid,
                    "status": current_status,
                    "detail": "disabled token is not checked",
                }
            if current_status == "error":
                return index, "abnormal", _mark_token_abnormal_for_check(
                    token_info,
                    detail="token already in request error status",
                )
            if current_status != "active":
                return index, "skipped", {
                    "token_id": tid,
                    "status": current_status,
                    "detail": f"unsupported token status: {current_status or 'unknown'}",
                }

            try:
                result = _check_token_invalid_or_expired(token_info)
            except Exception as exc:
                return index, "failed", {"token_id": tid, "detail": str(exc)}

            if result.get("result") == "invalid":
                updated = token_manager.report_invalid_by_identity(token_id=tid)
                profile_disabled = False
                disabled_profile_id = ""
                if isinstance(updated, dict):
                    try:
                        profile_disabled, disabled_profile_id = (
                            disable_auto_refresh_for_token_info(updated)
                        )
                    except Exception as exc:
                        result["auto_refresh_disable_error"] = str(exc)
                result.update(
                    {
                        "previous_status": (
                            updated.get("_previous_status")
                            if isinstance(updated, dict)
                            else current_status
                        ),
                        "status": "invalid",
                        "auto_refresh_disabled": profile_disabled,
                        "disabled_profile_id": disabled_profile_id or None,
                    }
                )
                return index, "invalid", result

            if result.get("result") == "valid":
                return index, "valid", result
            if result.get("result") == "abnormal":
                abnormal_payload = _mark_token_abnormal_for_check(
                    token_info,
                    detail=str(result.get("detail") or "").strip()
                    or "token marked as abnormal during invalid check",
                )
                abnormal_payload["status_code"] = result.get("status_code")
                return index, "abnormal", abnormal_payload
            return index, "skipped", result

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(check_one, index, tid)
                for index, tid in enumerate(token_ids)
            ]
            done_items = [future.result() for future in as_completed(futures)]

        done_items.sort(key=lambda item: item[0])
        for _, status, payload in done_items:
            checked.append(payload)
            if status == "invalid":
                invalid.append(payload)
                disabled_profile_id = str(
                    payload.get("disabled_profile_id") or ""
                ).strip()
                if disabled_profile_id:
                    disabled_profile_ids.append(disabled_profile_id)
            elif status == "abnormal":
                abnormal.append(payload)
                disabled_profile_id = str(
                    payload.get("disabled_profile_id") or ""
                ).strip()
                if disabled_profile_id:
                    disabled_profile_ids.append(disabled_profile_id)
                if str(payload.get("status") or "").strip().lower() == "abnormal":
                    disabled_tokens.append(str(payload.get("token_id") or "").strip())
            elif status == "valid":
                valid.append(payload)
            elif status == "failed":
                failed.append(payload)
            else:
                skipped.append(payload)

        changed_count = len(
            [
                item
                for item in invalid
                if str(item.get("previous_status") or "").strip().lower() != "invalid"
            ]
        )

        return {
            "status": "ok" if not failed else "partial",
            "total": len(token_ids),
            "checked_count": len(checked),
            "invalid_count": len(invalid),
            "changed_count": changed_count,
            "valid_count": len(valid),
            "abnormal_count": len(abnormal),
            "skipped_count": len(skipped),
            "failed_count": len(failed),
            "abnormal_changed_count": len({tid for tid in disabled_tokens if tid}),
            "disabled_auto_refresh_count": len(set(disabled_profile_ids)),
            "invalid": invalid,
            "valid": valid,
            "abnormal": abnormal,
            "skipped": skipped,
            "failed": failed,
        }

    @router.delete("/api/v1/tokens/{tid}")
    def delete_token(tid: str, request: Request):
        require_admin_auth(request)
        if not delete_token_and_linked_profile(tid):
            raise HTTPException(status_code=404, detail="token not found")
        return {"status": "ok"}

    @router.put("/api/v1/tokens/{tid}/status")
    def set_token_status(tid: str, status: str, request: Request):
        require_admin_auth(request)
        if status not in ("active", "disabled"):
            raise HTTPException(status_code=400, detail="Invalid status")
        token_info = token_manager.get_by_id(tid)
        if not token_info:
            raise HTTPException(status_code=404, detail="token not found")
        if status == "active" and token_info.get("status") in {"exhausted", "invalid", "abnormal"}:
            raise HTTPException(
                status_code=400,
                detail="exhausted/invalid/abnormal token cannot be reactivated; replace with a fresh token",
            )
        token_manager.set_status(tid, status)
        if status == "disabled" and token_info.get("auto_refresh"):
            profile_id = str(token_info.get("refresh_profile_id") or "").strip()
            if profile_id:
                try:
                    refresh_manager.set_enabled(profile_id, False)
                except Exception:
                    pass
        return {"status": "ok"}

    @router.post("/api/v1/tokens/{tid}/refresh")
    def refresh_token_now(tid: str, request: Request):
        require_admin_auth(request)
        token_info = token_manager.get_by_id(tid)
        if not token_info:
            raise HTTPException(status_code=404, detail="token not found")

        profile_id = str(token_info.get("refresh_profile_id") or "").strip()
        if not profile_id:
            raise HTTPException(
                status_code=400,
                detail="this token is not bound to an auto refresh profile",
            )

        try:
            result = refresh_manager.refresh_once(profile_id)
            return {"status": "ok", "result": result}
        except KeyError:
            raise HTTPException(status_code=404, detail="refresh profile not found")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @router.post("/api/v1/tokens/refresh-batch")
    def refresh_tokens_batch(req: TokenRefreshBatchRequest, request: Request):
        require_admin_auth(request)
        ids = req.ids if isinstance(req.ids, list) else None
        token_ids = [str(x or "").strip() for x in (ids or []) if str(x or "").strip()]
        if not token_ids:
            raise HTTPException(status_code=400, detail="ids is required")

        token_entries = []
        for index, tid in enumerate(token_ids):
            token_info = token_manager.get_by_id(tid) or {}
            token_entries.append(
                {
                    "index": index,
                    "token_id": tid,
                    "token_account_name": str(
                        token_info.get("refresh_profile_name") or ""
                    ).strip(),
                    "token_account_email": str(
                        token_info.get("refresh_profile_email") or ""
                    ).strip(),
                }
            )

        job_id = _create_token_refresh_job(token_entries=token_entries)
        _TOKEN_REFRESH_JOB_EXECUTOR.submit(
            _run_token_refresh_job,
            job_id,
            token_manager=token_manager,
            refresh_manager=refresh_manager,
            concurrency=get_batch_concurrency(),
        )
        return _build_token_refresh_job_payload(job_id) or {}

    @router.get("/api/v1/tokens/refresh-jobs/{job_id}")
    def get_token_refresh_job(job_id: str, request: Request):
        require_admin_auth(request)
        payload = _build_token_refresh_job_payload(job_id)
        if payload is None:
            raise HTTPException(status_code=404, detail="refresh job not found")
        return payload

    @router.put("/api/v1/tokens/{tid}/auto-refresh")
    def set_token_auto_refresh_enabled(tid: str, enabled: bool, request: Request):
        require_admin_auth(request)
        token_info = token_manager.get_by_id(tid)
        if not token_info:
            raise HTTPException(status_code=404, detail="token not found")

        profile_id = str(token_info.get("refresh_profile_id") or "").strip()
        if not profile_id:
            raise HTTPException(
                status_code=400,
                detail="this token is not bound to an auto refresh profile",
            )
        try:
            profile = refresh_manager.set_enabled(profile_id, bool(enabled))
            return {"status": "ok", "profile": profile}
        except KeyError:
            raise HTTPException(status_code=404, detail="refresh profile not found")

    @router.post("/api/v1/tokens/auto-refresh-batch")
    def set_tokens_auto_refresh_batch(
        req: TokenAutoRefreshBatchRequest, request: Request
    ):
        require_admin_auth(request)
        normalized_ids = [
            str(x or "").strip() for x in (req.ids or []) if str(x or "").strip()
        ]
        if not normalized_ids:
            raise HTTPException(status_code=400, detail="ids is required")

        updated = []
        skipped = []
        missing = []
        failed = []
        for tid in normalized_ids:
            token_info = token_manager.get_by_id(tid)
            if not token_info:
                missing.append(tid)
                continue
            profile_id = str(token_info.get("refresh_profile_id") or "").strip()
            if not profile_id:
                skipped.append(tid)
                continue
            try:
                refresh_manager.set_enabled(profile_id, bool(req.enabled))
                updated.append(tid)
            except KeyError:
                missing.append(tid)
            except Exception as exc:
                failed.append({"id": tid, "detail": str(exc)})

        if not updated and not skipped and not missing and not failed:
            raise HTTPException(status_code=400, detail="no token updated")

        return {
            "status": "ok" if not (skipped or missing or failed) else "partial",
            "enabled": bool(req.enabled),
            "updated_count": len(updated),
            "skipped_count": len(skipped),
            "missing_count": len(missing),
            "failed_count": len(failed),
            "updated_ids": updated,
            "skipped_ids": skipped,
            "missing_ids": missing,
            "failed": failed,
        }

    @router.post("/api/v1/tokens/{tid}/credits/refresh")
    def refresh_token_credits(tid: str, request: Request):
        require_admin_auth(request)
        token_info = token_manager.get_by_id(tid)
        if not token_info:
            raise HTTPException(status_code=404, detail="token not found")
        try:
            result = refresh_manager.refresh_credits_for_token_id(tid)
            return {"status": "ok", **result}
        except KeyError:
            raise HTTPException(status_code=404, detail="token not found")
        except Exception as exc:
            token_manager.set_credits_error(tid, str(exc))
            raise HTTPException(status_code=500, detail=str(exc))

    @router.post("/api/v1/tokens/credits/refresh-batch")
    def refresh_tokens_credits_batch(
        req: TokenCreditsBatchRefreshRequest, request: Request
    ):
        require_admin_auth(request)
        ids = req.ids if isinstance(req.ids, list) else None
        token_ids: List[str] = []
        if ids:
            token_ids = [str(x or "").strip() for x in ids if str(x or "").strip()]
        else:
            token_ids = token_manager.list_active_ids()

        if not token_ids:
            raise HTTPException(status_code=400, detail="no token to refresh")

        refreshed = []
        failed = []
        max_workers = min(get_batch_concurrency(), len(token_ids))

        def refresh_one(index: int, tid: str):
            try:
                return index, "ok", refresh_manager.refresh_credits_for_token_id(tid)
            except Exception as exc:
                token_manager.set_credits_error(tid, str(exc))
                return index, "failed", {"token_id": tid, "detail": str(exc)}

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(refresh_one, index, tid)
                for index, tid in enumerate(token_ids)
            ]
            done_items = [future.result() for future in as_completed(futures)]

        done_items.sort(key=lambda item: item[0])
        for _, status, payload in done_items:
            if status == "ok":
                refreshed.append(payload)
            else:
                failed.append(payload)

        return {
            "status": "ok" if not failed else "partial",
            "total": len(token_ids),
            "refreshed_count": len(refreshed),
            "failed_count": len(failed),
            "refreshed": refreshed,
            "failed": failed,
        }

    @router.post("/api/v1/tokens/success-counts/overwrite-from-logs")
    def overwrite_token_success_counts_from_logs(request: Request):
        require_admin_auth(request)
        log_summary = log_store.compute_generation_success_counts()
        result = token_manager.overwrite_success_counts(
            counts_by_token_id=log_summary.get("counts_by_token_id") or {},
            counts_by_email=log_summary.get("counts_by_email") or {},
            counts_by_name=log_summary.get("counts_by_name") or {},
            auto_disable_enabled=bool(
                config_manager.get("token_success_auto_disable_enabled", False)
            ),
            auto_disable_threshold=int(
                config_manager.get("token_success_auto_disable_threshold", 2) or 2
            ),
        )
        disabled_auto_refresh_profiles = 0
        for profile_id in result.get("exhausted_profile_ids") or []:
            pid = str(profile_id or "").strip()
            if not pid:
                continue
            try:
                refresh_manager.set_enabled(pid, False)
                disabled_auto_refresh_profiles += 1
            except Exception:
                continue
        response = {
            "status": "ok",
            "scanned_logs": int(log_summary.get("scanned_logs") or 0),
            "generation_logs": int(log_summary.get("generation_logs") or 0),
            "success_logs": int(log_summary.get("success_logs") or 0),
            "unidentified_success_logs": int(
                log_summary.get("unidentified_success_logs") or 0
            ),
            "disabled_auto_refresh_profiles": disabled_auto_refresh_profiles,
            **result,
        }
        logger.info(
            "token_success_backfill_overwrite scanned_logs=%s generation_logs=%s "
            "success_logs=%s unidentified_success_logs=%s matched_tokens=%s "
            "matched_by_token_id=%s matched_by_email=%s matched_by_name=%s "
            "changed_tokens=%s reset_to_zero_tokens=%s nonzero_success_tokens=%s "
            "total_success_count=%s exhausted_by_threshold=%s "
            "disabled_auto_refresh_profiles=%s",
            response["scanned_logs"],
            response["generation_logs"],
            response["success_logs"],
            response["unidentified_success_logs"],
            response["matched_tokens"],
            response["matched_by_token_id"],
            response["matched_by_email"],
            response["matched_by_name"],
            response["changed_tokens"],
            response["reset_to_zero_tokens"],
            response["nonzero_success_tokens"],
            response["total_success_count"],
            response["exhausted_by_threshold"],
            response["disabled_auto_refresh_profiles"],
        )
        return response

    @router.get("/api/v1/config")
    def get_config(request: Request):
        require_admin_auth(request)
        cfg = config_manager.get_all()
        cfg.pop("admin_session_secret", None)
        try:
            cfg.update(get_generated_storage_stats())
        except Exception:
            pass
        return cfg

    @router.put("/api/v1/config")
    def update_config(req: ConfigUpdateRequest, request: Request):
        require_admin_auth(request)
        incoming = req.model_dump(exclude_unset=True)
        if not incoming:
            return config_manager.get_all()

        update_data = {}
        if "api_key" in incoming:
            update_data["api_key"] = str(incoming["api_key"] or "").strip()
        if "admin_username" in incoming:
            admin_username = str(incoming["admin_username"] or "").strip()
            if not admin_username:
                raise HTTPException(
                    status_code=400, detail="admin_username cannot be empty"
                )
            update_data["admin_username"] = admin_username
        if "admin_password" in incoming:
            admin_password = str(incoming["admin_password"] or "")
            if not admin_password:
                raise HTTPException(
                    status_code=400, detail="admin_password cannot be empty"
                )
            update_data["admin_password"] = admin_password
        if "public_base_url" in incoming:
            update_data["public_base_url"] = str(
                incoming["public_base_url"] or ""
            ).strip()
        if "proxy" in incoming:
            update_data["proxy"] = str(incoming["proxy"] or "").strip()
        if "use_proxy" in incoming:
            update_data["use_proxy"] = bool(incoming["use_proxy"])
        if "resource_proxy" in incoming:
            update_data["resource_proxy"] = str(incoming["resource_proxy"] or "").strip()
        if "resource_use_proxy" in incoming:
            update_data["resource_use_proxy"] = bool(incoming["resource_use_proxy"])
        effective_basic_use_proxy = bool(
            update_data.get("use_proxy", config_manager.get("use_proxy", False))
        )
        effective_basic_proxy = str(
            update_data.get("proxy", config_manager.get("proxy", "")) or ""
        ).strip()
        if effective_basic_use_proxy and not effective_basic_proxy.startswith(("http://", "https://")):
            raise HTTPException(
                status_code=400,
                detail="proxy must start with http:// or https:// when basic proxy is enabled",
            )
        if "generate_timeout" in incoming:
            try:
                timeout_val = int(incoming["generate_timeout"])
            except Exception:
                timeout_val = 300
            update_data["generate_timeout"] = timeout_val if timeout_val > 0 else 300
        if "refresh_interval_hours" in incoming:
            try:
                interval_hours = int(incoming["refresh_interval_hours"])
            except Exception:
                raise HTTPException(
                    status_code=400,
                    detail="refresh_interval_hours must be an integer between 1 and 24",
                )
            if interval_hours < 1 or interval_hours > 24:
                raise HTTPException(
                    status_code=400,
                    detail="refresh_interval_hours must be between 1 and 24",
                )
            update_data["refresh_interval_hours"] = interval_hours
        if "retry_enabled" in incoming:
            update_data["retry_enabled"] = bool(incoming["retry_enabled"])
        if "retry_max_attempts" in incoming:
            try:
                retry_max_attempts = int(incoming["retry_max_attempts"])
            except Exception:
                raise HTTPException(
                    status_code=400, detail="retry_max_attempts must be an integer"
                )
            if retry_max_attempts < 1 or retry_max_attempts > 10:
                raise HTTPException(
                    status_code=400,
                    detail="retry_max_attempts must be between 1 and 10",
                )
            update_data["retry_max_attempts"] = retry_max_attempts
        if "retry_backoff_seconds" in incoming:
            try:
                retry_backoff_seconds = float(incoming["retry_backoff_seconds"])
            except Exception:
                raise HTTPException(
                    status_code=400,
                    detail="retry_backoff_seconds must be a number",
                )
            if retry_backoff_seconds < 0 or retry_backoff_seconds > 30:
                raise HTTPException(
                    status_code=400,
                    detail="retry_backoff_seconds must be between 0 and 30",
                )
            update_data["retry_backoff_seconds"] = retry_backoff_seconds
        if "retry_on_status_codes" in incoming:
            raw_codes = incoming["retry_on_status_codes"] or []
            if not isinstance(raw_codes, list):
                raise HTTPException(
                    status_code=400, detail="retry_on_status_codes must be a list"
                )
            status_codes: list[int] = []
            for item in raw_codes:
                try:
                    code = int(item)
                except Exception:
                    raise HTTPException(
                        status_code=400,
                        detail="retry_on_status_codes contains invalid value",
                    )
                if code < 100 or code > 599:
                    raise HTTPException(
                        status_code=400,
                        detail="retry_on_status_codes must be HTTP status codes",
                    )
                status_codes.append(code)
            update_data["retry_on_status_codes"] = sorted(set(status_codes))
        if "retry_on_error_types" in incoming:
            raw_types = incoming["retry_on_error_types"] or []
            if not isinstance(raw_types, list):
                raise HTTPException(
                    status_code=400, detail="retry_on_error_types must be a list"
                )
            error_types: list[str] = []
            for item in raw_types:
                txt = str(item or "").strip().lower()
                if txt:
                    error_types.append(txt)
            update_data["retry_on_error_types"] = sorted(set(error_types))
        if "token_rotation_strategy" in incoming:
            strategy = str(incoming["token_rotation_strategy"] or "").strip().lower()
            if strategy not in {"round_robin", "random"}:
                raise HTTPException(
                    status_code=400,
                    detail="token_rotation_strategy must be one of: round_robin, random",
                )
            update_data["token_rotation_strategy"] = strategy
        if "token_success_auto_disable_enabled" in incoming:
            update_data["token_success_auto_disable_enabled"] = bool(
                incoming["token_success_auto_disable_enabled"]
            )
        if "token_success_auto_disable_threshold" in incoming:
            try:
                token_success_auto_disable_threshold = int(
                    incoming["token_success_auto_disable_threshold"]
                )
            except Exception:
                raise HTTPException(
                    status_code=400,
                    detail="token_success_auto_disable_threshold must be an integer between 1 and 100000",
                )
            if (
                token_success_auto_disable_threshold < 1
                or token_success_auto_disable_threshold > 100000
            ):
                raise HTTPException(
                    status_code=400,
                    detail="token_success_auto_disable_threshold must be between 1 and 100000",
                )
            update_data["token_success_auto_disable_threshold"] = (
                token_success_auto_disable_threshold
            )
        if "batch_concurrency" in incoming:
            try:
                batch_concurrency = int(incoming["batch_concurrency"])
            except Exception:
                raise HTTPException(
                    status_code=400,
                    detail="batch_concurrency must be an integer between 1 and 100",
                )
            if batch_concurrency < 1 or batch_concurrency > 100:
                raise HTTPException(
                    status_code=400,
                    detail="batch_concurrency must be between 1 and 100",
                )
            update_data["batch_concurrency"] = batch_concurrency
        if "generated_max_size_mb" in incoming:
            try:
                generated_max_size_mb = int(incoming["generated_max_size_mb"])
            except Exception:
                raise HTTPException(
                    status_code=400,
                    detail="generated_max_size_mb must be an integer between 100 and 102400",
                )
            if generated_max_size_mb < 100 or generated_max_size_mb > 102400:
                raise HTTPException(
                    status_code=400,
                    detail="generated_max_size_mb must be between 100 and 102400",
                )
            update_data["generated_max_size_mb"] = generated_max_size_mb
        if "generated_prune_size_mb" in incoming:
            try:
                generated_prune_size_mb = int(incoming["generated_prune_size_mb"])
            except Exception:
                raise HTTPException(
                    status_code=400,
                    detail="generated_prune_size_mb must be an integer between 10 and 10240",
                )
            if generated_prune_size_mb < 10 or generated_prune_size_mb > 10240:
                raise HTTPException(
                    status_code=400,
                    detail="generated_prune_size_mb must be between 10 and 10240",
                )
            update_data["generated_prune_size_mb"] = generated_prune_size_mb
        if "use_upstream_result_url" in incoming:
            update_data["use_upstream_result_url"] = bool(
                incoming["use_upstream_result_url"]
            )
        if "imgbed_enabled" in incoming:
            update_data["imgbed_enabled"] = bool(incoming["imgbed_enabled"])
        if "imgbed_api_url" in incoming:
            update_data["imgbed_api_url"] = str(incoming["imgbed_api_url"] or "").strip()
        if "imgbed_api_key" in incoming:
            update_data["imgbed_api_key"] = str(incoming["imgbed_api_key"] or "").strip()
        effective_resource_use_proxy = bool(
            update_data.get(
                "resource_use_proxy", config_manager.get("resource_use_proxy", False)
            )
        )
        effective_resource_proxy = str(
            update_data.get("resource_proxy", config_manager.get("resource_proxy", ""))
            or ""
        ).strip()
        if effective_resource_use_proxy and not effective_resource_proxy.startswith(("http://", "https://")):
            raise HTTPException(
                status_code=400,
                detail="resource_proxy must start with http:// or https:// when resource proxy is enabled",
            )
        effective_imgbed_enabled = bool(
            update_data.get("imgbed_enabled", config_manager.get("imgbed_enabled", False))
        )
        effective_imgbed_api_url = str(
            update_data.get("imgbed_api_url", config_manager.get("imgbed_api_url", ""))
            or ""
        ).strip()
        effective_imgbed_api_key = str(
            update_data.get("imgbed_api_key", config_manager.get("imgbed_api_key", ""))
            or ""
        ).strip()
        if effective_imgbed_enabled:
            if not effective_imgbed_api_url.startswith(("http://", "https://")):
                raise HTTPException(
                    status_code=400,
                    detail="imgbed_api_url must start with http:// or https:// when imgbed is enabled",
                )
            if not effective_imgbed_api_key:
                raise HTTPException(
                    status_code=400,
                    detail="imgbed_api_key cannot be empty when imgbed is enabled",
                )
        effective_max = int(
            update_data.get(
                "generated_max_size_mb",
                config_manager.get("generated_max_size_mb", 1024),
            )
            or 1024
        )
        effective_prune = int(
            update_data.get(
                "generated_prune_size_mb",
                config_manager.get("generated_prune_size_mb", 200),
            )
            or 200
        )
        if effective_prune >= effective_max:
            raise HTTPException(
                status_code=400,
                detail="generated_prune_size_mb must be smaller than generated_max_size_mb",
            )
        config_manager.update_all(update_data)
        apply_client_config()
        return config_manager.get_all()

    @router.get("/api/v1/refresh-profiles")
    def refresh_profiles_list(request: Request):
        require_admin_auth(request)
        return {"profiles": refresh_manager.list_profiles()}

    @router.post("/api/v1/refresh-profiles/export-cookies")
    def refresh_profiles_export_cookies(req: ExportSelectionRequest, request: Request):
        require_admin_auth(request)
        token_ids = req.ids if isinstance(req.ids, list) else None
        profile_ids = None
        if token_ids:
            profile_ids = []
            seen = set()
            for tid in token_ids:
                token_info = token_manager.get_by_id(str(tid or "").strip())
                if not token_info:
                    continue
                profile_id = str(token_info.get("refresh_profile_id") or "").strip()
                if not profile_id or profile_id in seen:
                    continue
                seen.add(profile_id)
                profile_ids.append(profile_id)
        exported = refresh_manager.export_cookies(profile_ids)
        return {
            "status": "ok",
            "total": len(exported),
            "selected": bool(token_ids),
            "items": exported,
        }

    @router.post("/api/v1/refresh-profiles/import-cookie")
    def refresh_profiles_import_cookie(
        req: RefreshCookieImportRequest, request: Request
    ):
        route_started = time.perf_counter()
        require_admin_auth(request)
        profile_import_ms = 0.0
        refresh_call_ms = 0.0
        try:
            profile_import_started = time.perf_counter()
            profile = refresh_manager.import_cookie(req.cookie, name=req.name)
            profile_import_ms = _elapsed_ms(profile_import_started)
            refresh_result = None
            refresh_error = ""
            refresh_started = time.perf_counter()
            try:
                refresh_result = refresh_manager.refresh_once(
                    str(profile.get("id") or "")
                )
            except Exception as exc:
                refresh_error = str(exc)
            refresh_call_ms = _elapsed_ms(refresh_started)
            duplicate_count = (
                1
                if isinstance(refresh_result, dict)
                and bool(refresh_result.get("token_duplicate"))
                else 0
            )
            list_duplicate_count = (
                1
                if bool(profile.get("reused_existing_profile"))
                or (
                    isinstance(refresh_result, dict)
                    and bool(refresh_result.get("token_duplicate"))
                )
                else 0
            )
            failed_count = 1 if refresh_error else 0
            success_count = 0 if (failed_count or duplicate_count) else 1
            result = {
                "status": "ok" if not refresh_error else "partial",
                "profile": profile,
                "refresh_result": refresh_result,
                "refresh_error": refresh_error,
                "success_count": success_count,
                "failed_count": failed_count,
                "duplicate_count": duplicate_count,
                "request_duplicate_count": 0,
                "list_duplicate_count": list_duplicate_count,
                "overwritten_count": list_duplicate_count,
            }
            timing = (
                refresh_result.get("timing") or {}
                if isinstance(refresh_result, dict)
                else {}
            )
            response_timing = {
                "profile_import_ms": profile_import_ms,
                "refresh_call_ms": refresh_call_ms,
                "prepare_ms": _timing_value(timing, "prepare_ms"),
                "adobe_refresh_ms": _timing_value(timing, "adobe_refresh_ms"),
                "response_parse_ms": _timing_value(timing, "response_parse_ms"),
                "account_ms": _timing_value(timing, "account_ms"),
                "token_upsert_ms": _timing_value(timing, "token_upsert_ms"),
                "token_upsert_index_ms": _timing_value(
                    timing, "token_upsert_index_ms"
                ),
                "token_index_ms_sum": _timing_value(
                    timing, "token_upsert_index_ms"
                ),
                "token_index_ms_max": _timing_value(
                    timing, "token_upsert_index_ms"
                ),
                "token_upsert_total_ms": _timing_value(
                    timing, "token_upsert_total_ms"
                ),
                "credits_ms": _timing_value(timing, "credits_ms"),
                "total_ms": _elapsed_ms(route_started),
                "token_value_index_size": timing.get("token_value_index_size", 0)
                if isinstance(timing, dict)
                else 0,
                "token_profile_index_size": timing.get("token_profile_index_size", 0)
                if isinstance(timing, dict)
                else 0,
            }
            response_timing["token_index_success"] = _index_success_from_timing(
                response_timing
            )
            result["timing"] = response_timing
            logger.info(
                "import_cookie_timing status=%s success=%s failed=%s duplicate=%s "
                "list_duplicate=%s overwritten=%s profile_import_ms=%.3f "
                "refresh_call_ms=%.3f prepare_ms=%.3f adobe_refresh_ms=%.3f "
                "response_parse_ms=%.3f account_ms=%.3f token_upsert_ms=%.3f "
                "token_index_ms=%.3f token_upsert_total_ms=%.3f credits_ms=%.3f "
                "total_ms=%.3f token_value_index_size=%s token_profile_index_size=%s",
                result["status"],
                success_count,
                failed_count,
                duplicate_count,
                list_duplicate_count,
                list_duplicate_count,
                profile_import_ms,
                refresh_call_ms,
                _timing_value(timing, "prepare_ms"),
                _timing_value(timing, "adobe_refresh_ms"),
                _timing_value(timing, "response_parse_ms"),
                _timing_value(timing, "account_ms"),
                _timing_value(timing, "token_upsert_ms"),
                _timing_value(timing, "token_upsert_index_ms"),
                _timing_value(timing, "token_upsert_total_ms"),
                _timing_value(timing, "credits_ms"),
                response_timing["total_ms"],
                timing.get("token_value_index_size", 0)
                if isinstance(timing, dict)
                else 0,
                timing.get("token_profile_index_size", 0)
                if isinstance(timing, dict)
                else 0,
            )
            return result
        except ValueError as exc:
            logger.info(
                "import_cookie_timing status=failed success=0 failed=1 duplicate=0 "
                "list_duplicate=0 overwritten=0 profile_import_ms=%.3f "
                "refresh_call_ms=%.3f total_ms=%.3f error_stage=import",
                profile_import_ms,
                refresh_call_ms,
                _elapsed_ms(route_started),
            )
            raise HTTPException(status_code=400, detail=str(exc))

    @router.post("/api/v1/refresh-profiles/import-cookie-batch")
    def refresh_profiles_import_cookie_batch(
        req: RefreshCookieBatchImportRequest, request: Request
    ):
        require_admin_auth(request)
        if not req.items:
            raise HTTPException(status_code=400, detail="items is required")

        imported = []
        failed = []
        retained_by_cookie = {}
        invalid_entries = []

        request_dedupe_started = time.perf_counter()
        for idx, item in enumerate(req.items):
            fingerprint = refresh_manager.cookie_fingerprint(item.cookie)
            if not fingerprint:
                invalid_entries.append((idx, item))
                continue
            retained_by_cookie[fingerprint] = (idx, item)
        request_dedupe_ms = _elapsed_ms(request_dedupe_started)

        import_entries = sorted(
            [*retained_by_cookie.values(), *invalid_entries],
            key=lambda pair: pair[0],
        )
        request_duplicate_count = len(req.items) - len(import_entries)

        def import_one(idx: int, item):
            profile_import_started = time.perf_counter()
            try:
                profile = refresh_manager.import_cookie(item.cookie, name=item.name)
            except ValueError as exc:
                return {
                    "index": idx,
                    "imported": None,
                    "failed": {
                        "index": idx,
                        "name": item.name,
                        "detail": str(exc),
                    },
                    "refreshed": None,
                    "refresh_failed": None,
                    "timing": {
                        "profile_import_ms": _elapsed_ms(profile_import_started),
                    },
                }
            profile_import_ms = _elapsed_ms(profile_import_started)

            return {
                "index": idx,
                "imported": profile,
                "failed": None,
                "timing": {
                    "profile_import_ms": profile_import_ms,
                },
            }

        max_workers = min(get_batch_concurrency(), len(import_entries))
        if max_workers > 0:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [
                    executor.submit(import_one, idx, item)
                    for idx, item in import_entries
                ]
                done_items = [future.result() for future in as_completed(futures)]
        else:
            done_items = []

        done_items.sort(key=lambda item: item["index"])
        imported_entries = []
        for item in done_items:
            if item["imported"] is not None:
                imported.append(item["imported"])
                imported_entries.append(
                    {
                        "index": item["index"],
                        "profile": item["imported"],
                        "profile_import_ms": _timing_value(
                            item.get("timing"), "profile_import_ms"
                        ),
                    }
                )
            if item["failed"] is not None:
                failed.append(item["failed"])
        job_id = _create_import_refresh_job(
            total_requested=len(req.items),
            request_duplicate_count=request_duplicate_count,
            request_dedupe_ms=request_dedupe_ms,
            imported_entries=imported_entries,
            failed_entries=failed,
        )
        logger.info(
            "import_cookie_batch_queued status=%s total=%s processed=%s imported=%s "
            "failed=%s request_duplicate=%s job_id=%s request_dedupe_ms=%.3f",
            "queued" if imported else "failed",
            len(req.items),
            len(import_entries),
            len(imported),
            len(failed),
            request_duplicate_count,
            job_id,
            request_dedupe_ms,
        )
        if not imported:
            result = _build_import_refresh_payload(job_id) or {
                "status": "failed",
                "failed": failed,
            }
            raise HTTPException(status_code=400, detail=result)

        _IMPORT_REFRESH_JOB_EXECUTOR.submit(
            _run_import_refresh_job,
            job_id,
            refresh_manager=refresh_manager,
            concurrency=get_batch_concurrency(),
        )
        result = _build_import_refresh_payload(job_id) or {}
        return result

    @router.get("/api/v1/refresh-profiles/import-cookie-jobs/{job_id}")
    def refresh_profiles_import_cookie_job(job_id: str, request: Request):
        require_admin_auth(request)
        payload = _build_import_refresh_payload(job_id)
        if payload is None:
            raise HTTPException(status_code=404, detail="import job not found")
        return payload

    @router.post("/api/v1/refresh-profiles/{profile_id}/refresh-now")
    def refresh_profiles_refresh_now(profile_id: str, request: Request):
        require_admin_auth(request)
        try:
            return refresh_manager.refresh_once(profile_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="profile not found")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @router.put("/api/v1/refresh-profiles/{profile_id}/enabled")
    def refresh_profiles_set_enabled(
        profile_id: str, req: RefreshProfileEnabledRequest, request: Request
    ):
        require_admin_auth(request)
        try:
            profile = refresh_manager.set_enabled(profile_id, req.enabled)
            return {"status": "ok", "profile": profile}
        except KeyError:
            raise HTTPException(status_code=404, detail="profile not found")

    @router.delete("/api/v1/refresh-profiles/{profile_id}")
    def refresh_profiles_delete(profile_id: str, request: Request):
        require_admin_auth(request)
        try:
            refresh_manager.remove_profile(profile_id)
            return {"status": "ok"}
        except KeyError:
            raise HTTPException(status_code=404, detail="profile not found")

    return router
