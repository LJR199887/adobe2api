import threading
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from api.schemas import GenerateRequest


def _validate_prompt_length(prompt: str) -> None:
    if len(str(prompt or "").strip()) < 3:
        raise HTTPException(
            status_code=400,
            detail="prompt must contain at least 3 characters",
        )


def _normalize_upstream_request_error(exc: Exception) -> tuple[int, str, str] | None:
    message = str(exc or "").strip()
    lowered = message.lower()
    if ("poll failed: 400" in lowered or "submit failed: 400" in lowered) and (
        "validation error" in lowered
        or "字符串应至少包含 3 个字符" in message
        or "string should have at least 3 characters" in lowered
    ):
        return (
            400,
            "invalid_request_error",
            "prompt must contain at least 3 characters",
        )
    return None


def _resolve_sora_video_extras(data: dict) -> tuple[str, dict | None, dict | None]:
    locale = str(
        data.get("locale")
        or data.get("video_locale")
        or data.get("videoLocale")
        or "en-US"
    ).strip() or "en-US"
    if len(locale) > 32:
        locale = locale[:32]

    timeline_events = (
        data.get("timeline_events")
        or data.get("timelineEvents")
        or data.get("video_timeline_events")
        or data.get("videoTimelineEvents")
    )
    if not isinstance(timeline_events, dict):
        timeline_events = None
    elif not timeline_events:
        timeline_events = None

    audio = data.get("audio") or data.get("video_audio") or data.get("videoAudio")
    if not isinstance(audio, dict):
        audio = None
    elif not audio:
        audio = None

    return locale, timeline_events, audio


def _coerce_video_duration(value: Any, allowed: list[int], default: int) -> int:
    if value is None or str(value).strip() == "":
        return default
    try:
        parsed = int(str(value).strip().rstrip("sS"))
    except Exception:
        raise HTTPException(status_code=400, detail="unsupported duration")
    if parsed not in allowed:
        raise HTTPException(status_code=400, detail="unsupported duration")
    return parsed


def _coerce_video_resolution(
    value: Any, allowed: list[str], default: str | None
) -> str | None:
    if not allowed:
        return default
    if value is None or str(value).strip() == "":
        return default
    normalized = str(value).strip().lower()
    resolution_aliases = {
        "720": "720p",
        "720p": "720p",
        "1080": "1080p",
        "1080p": "1080p",
        "fhd": "1080p",
        "fullhd": "1080p",
    }
    resolved = resolution_aliases.get(normalized, normalized)
    if resolved not in allowed:
        raise HTTPException(status_code=400, detail="unsupported resolution")
    return resolved


def _resolve_video_request_config(model_id: str, data: dict, video_conf: dict) -> dict:
    resolved = dict(video_conf or {})
    allow_request_overrides = bool(resolved.get("allow_request_overrides"))

    if not allow_request_overrides:
        resolved["resolved_model_id"] = str(resolved.get("canonical_model") or model_id)
        return resolved

    duration_options = [
        int(item)
        for item in (resolved.get("duration_options") or [])
        if str(item).strip()
    ]
    aspect_ratio_options = [
        str(item).strip()
        for item in (resolved.get("aspect_ratio_options") or [])
        if str(item).strip()
    ]
    resolution_options = [
        str(item).strip().lower()
        for item in (resolved.get("resolution_options") or [])
        if str(item).strip()
    ]
    reference_mode_options = [
        str(item).strip().lower()
        for item in (resolved.get("reference_mode_options") or [])
        if str(item).strip()
    ]

    default_duration = int(resolved.get("duration") or (duration_options[0] if duration_options else 8))
    default_ratio = str(
        resolved.get("aspect_ratio") or (aspect_ratio_options[0] if aspect_ratio_options else "16:9")
    ).strip()
    default_resolution = (
        str(resolved.get("resolution") or (resolution_options[0] if resolution_options else "")).strip().lower()
        or None
    )
    default_reference_mode = str(
        resolved.get("reference_mode") or (reference_mode_options[0] if reference_mode_options else "frame")
    ).strip().lower()

    requested_ratio = str(data.get("aspect_ratio") or "").strip()
    if not requested_ratio and aspect_ratio_options:
        requested_ratio = default_ratio
    if requested_ratio and aspect_ratio_options and requested_ratio not in aspect_ratio_options:
        raise HTTPException(status_code=400, detail="unsupported aspect_ratio")

    requested_resolution = (
        data.get("resolution")
        or data.get("video_resolution")
        or data.get("output_resolution")
    )
    requested_reference_mode = str(
        data.get("reference_mode") or data.get("video_reference_mode") or default_reference_mode
    ).strip().lower() or default_reference_mode
    if reference_mode_options and requested_reference_mode not in reference_mode_options:
        raise HTTPException(status_code=400, detail="unsupported reference_mode")

    resolved["duration"] = _coerce_video_duration(
        data.get("duration") or data.get("video_duration"),
        duration_options,
        default_duration,
    )
    resolved["aspect_ratio"] = requested_ratio or default_ratio
    resolved["resolution"] = _coerce_video_resolution(
        requested_resolution,
        resolution_options,
        default_resolution,
    )
    resolved["reference_mode"] = requested_reference_mode
    resolved["resolved_model_id"] = str(resolved.get("canonical_model") or model_id)
    return resolved


def build_generation_router(
    *,
    store,
    request_log_store,
    live_request_store,
    token_manager,
    client,
    generated_dir: Path,
    model_catalog: dict,
    video_model_catalog: dict,
    supported_ratios: set,
    resolve_model: Callable[[str | None], dict],
    resolve_ratio_and_resolution: Callable[[dict, str | None], tuple[str, str, str]],
    require_service_api_key: Callable[[Request], None],
    set_request_task_progress: Callable[..., None],
    run_with_token_retries: Callable[..., Any],
    set_request_error_detail: Callable[..., str],
    set_request_preview: Callable[[Request, str, str], None],
    public_image_url: Callable[[Request, str], str],
    public_generated_url: Callable[[Request, str], str],
    resolve_video_options: Callable[[dict], tuple[bool, str, str]],
    load_input_images: Callable[[Any], list[tuple[bytes, str]]],
    prepare_video_source_image: Callable[[bytes, str, str], tuple[bytes, str]],
    video_ext_from_meta: Callable[[dict], str],
    extract_prompt_from_messages: Callable[[Any], str],
    sse_chat_stream: Callable[[dict], Any],
    on_generated_file_written: Callable[[Path, int, int], None],
    quota_error_cls,
    auth_error_cls,
    upstream_temp_error_cls,
    logger,
) -> APIRouter:
    router = APIRouter()

    def _current_request_id(request: Request) -> str:
        return str(getattr(request.state, "log_id", "") or "").strip()

    def _attach_request_id(payload: dict, request: Request) -> dict:
        content = dict(payload or {})
        request_id = _current_request_id(request)
        if request_id:
            content["request_id"] = request_id
        return content

    def _json_response(status_code: int, content: dict, request: Request) -> JSONResponse:
        return JSONResponse(
            status_code=status_code,
            content=_attach_request_id(content, request),
        )

    def _build_request_status_payload(
        request_id: str, item: dict, source: str
    ) -> dict:
        task_status = str(item.get("task_status") or "").upper() or None
        preview_url = str(item.get("preview_url") or "").strip() or None
        preview_kind = str(item.get("preview_kind") or "").strip() or None
        error_text = str(item.get("error") or "").strip() or None
        error_code = str(item.get("error_code") or "").strip() or None
        operation = str(item.get("operation") or "").strip() or None
        model = str(item.get("model") or "").strip() or None
        model_params = str(item.get("model_params") or "").strip() or None
        prompt_preview = str(item.get("prompt_preview") or "").strip() or None
        upstream_job_id = str(item.get("upstream_job_id") or "").strip() or None
        attempt_id = str(item.get("id") or "").strip() or None
        if attempt_id == request_id:
            attempt_id = None
        retry_after = item.get("retry_after")
        status_code = item.get("status_code")
        try:
            task_progress = (
                round(float(item.get("task_progress")), 2)
                if item.get("task_progress") is not None
                else None
            )
        except Exception:
            task_progress = None
        try:
            status_code = int(status_code) if status_code is not None else None
        except Exception:
            status_code = None
        try:
            retry_after = int(retry_after) if retry_after is not None else None
        except Exception:
            retry_after = None
        done = task_status in {"COMPLETED", "FAILED"} or bool(
            status_code is not None and status_code >= 400
        )
        payload = {
            "request_id": request_id,
            "task_status": task_status,
            "task_progress": task_progress,
            "upstream_job_id": upstream_job_id,
            "retry_after": retry_after,
            "preview_url": preview_url,
            "preview_kind": preview_kind,
            "error": error_text,
            "error_code": error_code,
            "operation": operation,
            "model": model,
            "model_params": model_params,
            "prompt_preview": prompt_preview,
            "status_code": status_code,
            "source": source,
            "done": done,
        }
        if attempt_id:
            payload["attempt_id"] = attempt_id
        return payload

    @router.get("/v1/models")
    def list_models(request: Request):
        require_service_api_key(request)
        data = []
        for model_id, conf in model_catalog.items():
            if conf.get("hidden"):
                continue
            item = {
                "id": model_id,
                "object": "model",
                "owned_by": "adobe2api",
                "description": conf["description"],
            }
            parameters = {}
            if conf.get("output_resolution_options"):
                parameters["output_resolution"] = conf["output_resolution_options"]
            if conf.get("aspect_ratio_options"):
                parameters["aspect_ratio"] = conf["aspect_ratio_options"]
            if parameters:
                item["parameters"] = parameters
            data.append(
                item
            )
        for model_id, conf in video_model_catalog.items():
            if conf.get("hidden"):
                continue
            item = {
                "id": model_id,
                "object": "model",
                "owned_by": "adobe2api",
                "description": conf["description"],
            }
            parameters = {}
            if conf.get("duration_options"):
                parameters["duration"] = conf["duration_options"]
            if conf.get("aspect_ratio_options"):
                parameters["aspect_ratio"] = conf["aspect_ratio_options"]
            if conf.get("resolution_options"):
                parameters["resolution"] = conf["resolution_options"]
            if conf.get("reference_mode_options"):
                parameters["reference_mode"] = conf["reference_mode_options"]
            if parameters:
                item["parameters"] = parameters
            data.append(
                item
            )
        return {"object": "list", "data": data}

    @router.get("/v1/requests/{request_id}")
    def get_request_status(request_id: str, request: Request):
        require_service_api_key(request)

        normalized_id = str(request_id or "").strip()
        if not normalized_id:
            raise HTTPException(status_code=400, detail="request_id is required")

        live_item = live_request_store.get(normalized_id)
        if isinstance(live_item, dict):
            return _build_request_status_payload(
                normalized_id,
                live_item,
                source="live",
            )

        log_item = request_log_store.get(normalized_id)
        if isinstance(log_item, dict):
            return _build_request_status_payload(
                normalized_id,
                log_item,
                source="log",
            )

        raise HTTPException(status_code=404, detail="request not found")

    @router.post("/v1/images/generations")
    def openai_generate(data: dict, request: Request):
        require_service_api_key(request)

        prompt = data.get("prompt", "").strip()
        if not prompt:
            return _json_response(
                status_code=400,
                content={
                    "error": {
                        "message": "prompt is required",
                        "type": "invalid_request_error",
                    }
                },
                request=request,
            )
        _validate_prompt_length(prompt)

        model_id = data.get("model")
        if str(model_id or "").strip() in video_model_catalog:
            return _json_response(
                status_code=400,
                content={
                    "error": {
                        "message": "Use /v1/chat/completions for video generation",
                        "type": "invalid_request_error",
                    }
                },
                request=request,
            )
        ratio, output_resolution, resolved_model_id = resolve_ratio_and_resolution(
            data, model_id
        )
        model_conf = resolve_model(resolved_model_id)

        try:
            set_request_task_progress(
                request, task_status="IN_PROGRESS", task_progress=0.0
            )

            def _run_once(token: str):
                def _image_progress_cb(update: dict):
                    set_request_task_progress(
                        request,
                        task_status=str(update.get("task_status") or "IN_PROGRESS"),
                        task_progress=update.get("task_progress"),
                        upstream_job_id=update.get("upstream_job_id"),
                        retry_after=update.get("retry_after"),
                        error=update.get("error"),
                    )

                job_id = uuid.uuid4().hex
                out_path = generated_dir / f"{job_id}.png"
                old_size = 0
                try:
                    if out_path.exists():
                        old_size = int(out_path.stat().st_size)
                except Exception:
                    old_size = 0

                image_bytes, _meta = client.generate(
                    token=token,
                    prompt=prompt,
                    aspect_ratio=ratio,
                    output_resolution=output_resolution,
                    upstream_model_id=str(
                        model_conf.get("upstream_model_id") or "gemini-flash"
                    ),
                    upstream_model_version=str(
                        model_conf.get("upstream_model_version") or "nano-banana-2"
                    ),
                    timeout=client.generate_timeout,
                    out_path=out_path,
                    progress_cb=_image_progress_cb,
                )
                if image_bytes is not None:
                    out_path.write_bytes(image_bytes)
                new_size = int(out_path.stat().st_size) if out_path.exists() else 0
                on_generated_file_written(out_path, old_size, new_size)
                image_url = public_image_url(request, job_id)
                set_request_preview(request, image_url, kind="image")
                return _attach_request_id({
                    "created": int(time.time()),
                    "model": resolved_model_id,
                    "data": [{"url": image_url}],
                }, request)

            return run_with_token_retries(
                request=request,
                operation_name="images.generations",
                run_once=_run_once,
            )

        except quota_error_cls:
            error_code = str(
                getattr(request.state, "log_error_code", "") or ""
            ) or set_request_error_detail(
                request,
                error="Token quota exhausted",
                status_code=429,
                error_type="rate_limit_error",
                include_traceback=False,
            )
            set_request_task_progress(
                request,
                task_status="FAILED",
                task_progress=0.0,
                error="Token quota exhausted",
            )
            return _json_response(
                status_code=429,
                content={
                    "error": {
                        "message": "Token quota exhausted",
                        "type": "rate_limit_error",
                        "code": error_code,
                    }
                },
                request=request,
            )
        except auth_error_cls:
            error_code = str(
                getattr(request.state, "log_error_code", "") or ""
            ) or set_request_error_detail(
                request,
                error="Token invalid or expired",
                status_code=401,
                error_type="authentication_error",
                include_traceback=False,
            )
            set_request_task_progress(
                request,
                task_status="FAILED",
                task_progress=0.0,
                error="Token invalid or expired",
            )
            return _json_response(
                status_code=401,
                content={
                    "error": {
                        "message": "Token invalid or expired",
                        "type": "authentication_error",
                        "code": error_code,
                    }
                },
                request=request,
            )
        except upstream_temp_error_cls as exc:
            error_code = str(
                getattr(request.state, "log_error_code", "") or ""
            ) or set_request_error_detail(
                request,
                error=exc,
                status_code=503,
                error_type="server_error",
                include_traceback=False,
            )
            set_request_task_progress(
                request, task_status="FAILED", task_progress=0.0, error=str(exc)
            )
            return _json_response(
                status_code=503,
                content={
                    "error": {
                        "message": str(exc),
                        "type": "server_error",
                        "code": error_code,
                    }
                },
                request=request,
            )
        except HTTPException as exc:
            err_type = (
                "invalid_request_error"
                if 400 <= int(exc.status_code) < 500
                else "server_error"
            )
            error_code = set_request_error_detail(
                request,
                error=str(exc.detail),
                status_code=exc.status_code,
                error_type=err_type,
                include_traceback=False,
            )
            set_request_task_progress(
                request, task_status="FAILED", task_progress=0.0, error=str(exc.detail)
            )
            return _json_response(
                status_code=exc.status_code,
                content={
                    "error": {
                        "message": str(exc.detail),
                        "type": err_type,
                        "code": error_code,
                    }
                },
                request=request,
            )
        except Exception as exc:
            normalized = _normalize_upstream_request_error(exc)
            if normalized is not None:
                status_code, err_type, message = normalized
                error_code = set_request_error_detail(
                    request,
                    error=message,
                    status_code=status_code,
                    error_type=err_type,
                    include_traceback=False,
                )
                set_request_task_progress(
                    request, task_status="FAILED", task_progress=0.0, error=message
                )
                return _json_response(
                    status_code=status_code,
                    content={
                        "error": {
                            "message": message,
                            "type": err_type,
                            "code": error_code,
                        }
                    },
                    request=request,
                )
            error_code = set_request_error_detail(
                request,
                error=exc,
                status_code=500,
                error_type="server_error",
                include_traceback=True,
            )
            logger.exception(
                "Unhandled error in /v1/images/generations log_id=%s model=%s",
                getattr(request.state, "log_id", ""),
                resolved_model_id,
            )
            set_request_task_progress(
                request, task_status="FAILED", task_progress=0.0, error=str(exc)
            )
            return _json_response(
                status_code=500,
                content={
                    "error": {
                        "message": str(exc),
                        "type": "server_error",
                        "code": error_code,
                    }
                },
                request=request,
            )

    @router.post("/api/v1/generate")
    def create_job(data: GenerateRequest, request: Request):
        require_service_api_key(request)

        prompt = data.prompt.strip()
        if not prompt:
            raise HTTPException(status_code=400, detail="prompt cannot be empty")
        _validate_prompt_length(prompt)

        ratio = data.aspect_ratio.strip() or "16:9"
        if ratio not in supported_ratios:
            raise HTTPException(status_code=400, detail="unsupported aspect ratio")

        output_resolution = (data.output_resolution or "2K").upper()
        if output_resolution not in {"1K", "2K", "4K"}:
            raise HTTPException(status_code=400, detail="unsupported output_resolution")

        model_conf = resolve_model(data.model)
        if data.model:
            output_resolution = model_conf["output_resolution"]

        job = store.create(prompt=prompt, aspect_ratio=ratio)

        def runner(job_id: str):
            store.update(job_id, status="running", progress=5.0)
            max_attempts = client.retry_max_attempts if client.retry_enabled else 1
            max_attempts = max(1, int(max_attempts))
            last_error = "No active tokens available in the pool"

            for attempt in range(1, max_attempts + 1):
                token = token_manager.get_available(
                    strategy=client.token_rotation_strategy
                )
                if not token:
                    break

                try:
                    out_path = generated_dir / f"{job_id}.png"
                    old_size = 0
                    try:
                        if out_path.exists():
                            old_size = int(out_path.stat().st_size)
                    except Exception:
                        old_size = 0

                    image_bytes, meta = client.generate(
                        token=token,
                        prompt=prompt,
                        aspect_ratio=ratio,
                        output_resolution=output_resolution,
                        upstream_model_id=str(
                            model_conf.get("upstream_model_id") or "gemini-flash"
                        ),
                        upstream_model_version=str(
                            model_conf.get("upstream_model_version") or "nano-banana-2"
                        ),
                        out_path=out_path,
                    )
                    if image_bytes is not None:
                        out_path.write_bytes(image_bytes)
                    new_size = int(out_path.stat().st_size) if out_path.exists() else 0
                    on_generated_file_written(out_path, old_size, new_size)
                    progress = float(meta.get("progress") or 100.0)
                    image_url = public_image_url(request, job_id)
                    store.update(
                        job_id,
                        status="succeeded",
                        progress=max(progress, 100.0),
                        image_url=image_url,
                    )
                    return
                except quota_error_cls:
                    token_manager.report_exhausted(token)
                    last_error = "Token quota exhausted."
                    retryable = attempt < max_attempts
                except auth_error_cls:
                    token_manager.report_invalid(token)
                    last_error = "Token invalid or expired."
                    retryable = attempt < max_attempts
                except upstream_temp_error_cls as exc:
                    last_error = str(exc)
                    retryable = (
                        attempt < max_attempts
                        and client.should_retry_temporary_error(exc)
                    )
                except Exception as exc:
                    store.update(job_id, status="failed", error=str(exc))
                    return

                if retryable:
                    delay = client._retry_delay_for_attempt(attempt)
                    if delay > 0:
                        time.sleep(delay)
                    continue
                break

            store.update(job_id, status="failed", error=last_error)

        threading.Thread(target=runner, args=(job.id,), daemon=True).start()

        return _attach_request_id({"task_id": job.id, "status": job.status}, request)

    @router.get("/api/v1/generate/{task_id}")
    def get_job(task_id: str, request: Request):
        require_service_api_key(request)

        job = store.get(task_id)
        if not job:
            raise HTTPException(status_code=404, detail="task not found")
        return asdict(job)

    @router.post("/v1/chat/completions")
    def chat_completions(data: dict, request: Request):
        require_service_api_key(request)

        prompt = extract_prompt_from_messages(data.get("messages") or [])
        if not prompt:
            prompt = str(data.get("prompt") or "").strip()
        if not prompt:
            return _json_response(
                status_code=400,
                content={
                    "error": {
                        "message": "messages or prompt is required",
                        "type": "invalid_request_error",
                    }
                },
                request=request,
            )

        model_id = str(data.get("model") or "").strip()
        if (
            model_id.startswith("sora2")
            or model_id.startswith("veo31-fast")
            or model_id.startswith("veo31-")
            or model_id.startswith("firefly-sora2")
            or model_id.startswith("firefly-veo31-fast")
            or model_id.startswith("firefly-veo31-")
        ) and model_id not in video_model_catalog:
            return _json_response(
                status_code=400,
                content={
                    "error": {
                        "message": "Invalid video model. Use /v1/models to get supported sora2, sora2-pro, veo31, veo31-ref or veo31-fast models, then pass duration/aspect_ratio/resolution/reference_mode in the request body.",
                        "type": "invalid_request_error",
                    }
                },
                request=request,
            )
        video_conf = video_model_catalog.get(model_id)
        is_video_model = video_conf is not None
        if not is_video_model:
            _validate_prompt_length(prompt)
        resolved_video_conf = (
            _resolve_video_request_config(model_id, data, video_conf or {})
            if is_video_model
            else {}
        )
        resolved_model_id = (
            str(resolved_video_conf.get("resolved_model_id") or model_id)
            if is_video_model
            else None
        )
        ratio = "9:16"
        output_resolution = "2K"
        duration = int(resolved_video_conf["duration"]) if is_video_model else 12
        video_resolution = (
            str(resolved_video_conf.get("resolution") or "720p")
            if is_video_model
            else "720p"
        )
        if is_video_model:
            ratio = str(resolved_video_conf.get("aspect_ratio") or ratio)
        video_engine = (
            str(resolved_video_conf.get("engine") or "sora2") if is_video_model else ""
        )
        generate_audio = True
        negative_prompt = ""
        video_locale = "en-US"
        timeline_events = None
        video_audio = None
        video_reference_mode = (
            str(resolved_video_conf.get("reference_mode") or "frame")
            if is_video_model
            else "frame"
        )
        if is_video_model:
            resolved_video_options = resolve_video_options(data)
            if (
                isinstance(resolved_video_options, tuple)
                and len(resolved_video_options) == 3
            ):
                generate_audio, negative_prompt, requested_reference_mode = (
                    resolved_video_options
                )
                if "reference_mode" not in (video_conf or {}):
                    video_reference_mode = requested_reference_mode
            else:
                generate_audio, negative_prompt = resolved_video_options
            video_locale, timeline_events, video_audio = _resolve_sora_video_extras(data)
        else:
            ratio, output_resolution, resolved_model_id = resolve_ratio_and_resolution(
                data, model_id or None
            )
        image_model_conf = (
            resolve_model(resolved_model_id) if not is_video_model else {}
        )

        try:
            input_images = load_input_images(data.get("messages") or [])
            set_request_task_progress(
                request, task_status="IN_PROGRESS", task_progress=0.0
            )

            def _run_once(token: str):
                source_image_ids: list[str] = []
                image_url = ""
                response_content = ""

                if is_video_model:
                    if (
                        video_engine == "veo31-standard"
                        and video_reference_mode == "image"
                    ):
                        max_video_inputs = 3
                    else:
                        max_video_inputs = (
                            2 if video_engine in {"veo31-fast", "veo31-standard"} else 1
                        )
                    if len(input_images) > max_video_inputs:
                        raise HTTPException(
                            status_code=400,
                            detail=f"video model supports at most {max_video_inputs} input image(s)",
                        )
                    for image_bytes, _image_mime in input_images[:max_video_inputs]:
                        prepared_bytes, prepared_mime = prepare_video_source_image(
                            image_bytes,
                            ratio,
                            video_resolution,
                        )
                        source_image_ids.append(
                            client.upload_image(token, prepared_bytes, prepared_mime)
                        )

                    def _video_progress_cb(update: dict):
                        set_request_task_progress(
                            request,
                            task_status=str(update.get("task_status") or "IN_PROGRESS"),
                            task_progress=update.get("task_progress"),
                            upstream_job_id=update.get("upstream_job_id"),
                            retry_after=update.get("retry_after"),
                            error=update.get("error"),
                        )

                    job_id = uuid.uuid4().hex
                    tmp_path = generated_dir / f"{job_id}.video.tmp"
                    old_size = 0
                    try:
                        if tmp_path.exists():
                            old_size = int(tmp_path.stat().st_size)
                    except Exception:
                        old_size = 0

                    video_bytes, video_meta = client.generate_video(
                        token=token,
                        video_conf=resolved_video_conf or {},
                        prompt=prompt,
                        aspect_ratio=ratio,
                        duration=duration,
                        source_image_ids=source_image_ids,
                        timeout=max(int(client.generate_timeout), 600),
                        negative_prompt=negative_prompt,
                        generate_audio=generate_audio,
                        locale=video_locale,
                        timeline_events=timeline_events,
                        audio=video_audio,
                        reference_mode=video_reference_mode,
                        out_path=tmp_path,
                        progress_cb=_video_progress_cb,
                    )
                    video_ext = video_ext_from_meta(video_meta)
                    filename = f"{job_id}.{video_ext}"
                    out_path = generated_dir / filename
                    if video_bytes is not None:
                        out_path.write_bytes(video_bytes)
                    elif tmp_path.exists():
                        tmp_path.replace(out_path)
                    new_size = int(out_path.stat().st_size) if out_path.exists() else 0
                    on_generated_file_written(out_path, old_size, new_size)
                    image_url = public_generated_url(request, filename)
                    set_request_preview(request, image_url, kind="video")
                    response_content = (
                        f"```html\n<video src='{image_url}' controls></video>\n```"
                    )
                else:
                    for image_bytes, image_mime in input_images:
                        source_image_ids.append(
                            client.upload_image(
                                token, image_bytes, image_mime or "image/jpeg"
                            )
                        )

                    def _image_progress_cb(update: dict):
                        set_request_task_progress(
                            request,
                            task_status=str(update.get("task_status") or "IN_PROGRESS"),
                            task_progress=update.get("task_progress"),
                            upstream_job_id=update.get("upstream_job_id"),
                            retry_after=update.get("retry_after"),
                            error=update.get("error"),
                        )

                    job_id = uuid.uuid4().hex
                    out_path = generated_dir / f"{job_id}.png"
                    old_size = 0
                    try:
                        if out_path.exists():
                            old_size = int(out_path.stat().st_size)
                    except Exception:
                        old_size = 0

                    image_bytes, _meta = client.generate(
                        token=token,
                        prompt=prompt,
                        aspect_ratio=ratio,
                        output_resolution=output_resolution,
                        upstream_model_id=str(
                            image_model_conf.get("upstream_model_id") or "gemini-flash"
                        ),
                        upstream_model_version=str(
                            image_model_conf.get("upstream_model_version")
                            or "nano-banana-2"
                        ),
                        source_image_ids=source_image_ids,
                        timeout=client.generate_timeout,
                        out_path=out_path,
                        progress_cb=_image_progress_cb,
                    )
                    if image_bytes is not None:
                        out_path.write_bytes(image_bytes)
                    new_size = int(out_path.stat().st_size) if out_path.exists() else 0
                    on_generated_file_written(out_path, old_size, new_size)
                    image_url = public_image_url(request, job_id)
                    set_request_preview(request, image_url, kind="image")
                    response_content = f"![Generated Image]({image_url})"

                response_payload = {
                    "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": resolved_model_id,
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": response_content,
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": 0,
                    },
                }
                if bool(data.get("stream", False)):
                    return StreamingResponse(
                        sse_chat_stream(response_payload),
                        media_type="text/event-stream",
                    )
                return _attach_request_id(response_payload, request)

            return run_with_token_retries(
                request=request,
                operation_name="chat.completions",
                run_once=_run_once,
            )
        except quota_error_cls:
            error_code = str(
                getattr(request.state, "log_error_code", "") or ""
            ) or set_request_error_detail(
                request,
                error="Token quota exhausted",
                status_code=429,
                error_type="rate_limit_error",
                include_traceback=False,
            )
            set_request_task_progress(
                request,
                task_status="FAILED",
                task_progress=0.0,
                error="Token quota exhausted",
            )
            return _json_response(
                status_code=429,
                content={
                    "error": {
                        "message": "Token quota exhausted",
                        "type": "rate_limit_error",
                        "code": error_code,
                    }
                },
                request=request,
            )
        except auth_error_cls:
            error_code = str(
                getattr(request.state, "log_error_code", "") or ""
            ) or set_request_error_detail(
                request,
                error="Token invalid or expired",
                status_code=401,
                error_type="authentication_error",
                include_traceback=False,
            )
            set_request_task_progress(
                request,
                task_status="FAILED",
                task_progress=0.0,
                error="Token invalid or expired",
            )
            return _json_response(
                status_code=401,
                content={
                    "error": {
                        "message": "Token invalid or expired",
                        "type": "authentication_error",
                        "code": error_code,
                    }
                },
                request=request,
            )
        except upstream_temp_error_cls as exc:
            error_code = str(
                getattr(request.state, "log_error_code", "") or ""
            ) or set_request_error_detail(
                request,
                error=exc,
                status_code=503,
                error_type="server_error",
                include_traceback=False,
            )
            set_request_task_progress(
                request, task_status="FAILED", task_progress=0.0, error=str(exc)
            )
            return _json_response(
                status_code=503,
                content={
                    "error": {
                        "message": str(exc),
                        "type": "server_error",
                        "code": error_code,
                    }
                },
                request=request,
            )
        except HTTPException as exc:
            err_type = (
                "invalid_request_error"
                if 400 <= int(exc.status_code) < 500
                else "server_error"
            )
            error_code = set_request_error_detail(
                request,
                error=str(exc.detail),
                status_code=exc.status_code,
                error_type=err_type,
                include_traceback=False,
            )
            set_request_task_progress(
                request, task_status="FAILED", task_progress=0.0, error=str(exc.detail)
            )
            return _json_response(
                status_code=exc.status_code,
                content={
                    "error": {
                        "message": str(exc.detail),
                        "type": err_type,
                        "code": error_code,
                    }
                },
                request=request,
            )
        except Exception as exc:
            normalized = _normalize_upstream_request_error(exc)
            if normalized is not None:
                status_code, err_type, message = normalized
                error_code = set_request_error_detail(
                    request,
                    error=message,
                    status_code=status_code,
                    error_type=err_type,
                    include_traceback=False,
                )
                set_request_task_progress(
                    request, task_status="FAILED", task_progress=0.0, error=message
                )
                return _json_response(
                    status_code=status_code,
                    content={
                        "error": {
                            "message": message,
                            "type": err_type,
                            "code": error_code,
                        }
                    },
                    request=request,
                )
            error_code = set_request_error_detail(
                request,
                error=exc,
                status_code=500,
                error_type="server_error",
                include_traceback=True,
            )
            logger.exception(
                "Unhandled error in /v1/chat/completions log_id=%s model=%s resolved_model=%s is_video_model=%s",
                getattr(request.state, "log_id", ""),
                model_id,
                resolved_model_id,
                is_video_model,
            )
            set_request_task_progress(
                request, task_status="FAILED", task_progress=0.0, error=str(exc)
            )
            return _json_response(
                status_code=500,
                content={
                    "error": {
                        "message": str(exc),
                        "type": "server_error",
                        "code": error_code,
                    }
                },
                request=request,
            )

    return router
