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


def _extract_upstream_asset_url(meta: dict, asset_kind: str) -> str:
    outputs = meta.get("outputs") or []
    if not outputs:
        return ""
    asset = (outputs[0] or {}).get(asset_kind) or {}
    return str(asset.get("presignedUrl") or "").strip()


def _video_mime_type(video_ext: str) -> str:
    normalized = str(video_ext or "").strip().lower()
    if normalized == "mov":
        return "video/quicktime"
    if normalized == "webm":
        return "video/webm"
    return "video/mp4"


def _looks_like_video_model_id(model_id: str) -> bool:
    normalized = str(model_id or "").strip().lower()
    return normalized.startswith(
        (
            "sora2",
            "veo31-fast",
            "veo31-",
            "firefly-sora2",
            "firefly-veo31-fast",
            "firefly-veo31-",
        )
    )


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
    use_upstream_result_url: Callable[[], bool],
    use_imgbed_upload: Callable[[], bool],
    upload_generated_asset_to_imgbed: Callable[[str, str, str | None], str],
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

    def _json_response(status_code: int, content: dict, request: Request) -> JSONResponse:
        return JSONResponse(status_code=status_code, content=content)

    def _normalize_image_request_data(data: dict, prompt: str) -> dict:
        normalized = dict(data or {})
        messages = normalized.get("messages")
        if isinstance(messages, list) and messages:
            return normalized

        image_urls: list[str] = []
        seen_urls: set[str] = set()

        def _append_image_url(value: Any) -> None:
            raw_value = value
            if isinstance(raw_value, dict):
                raw_value = (
                    raw_value.get("url")
                    or raw_value.get("image_url")
                    or raw_value.get("src")
                )
            text = str(raw_value or "").strip()
            if not text or text in seen_urls:
                return
            seen_urls.add(text)
            image_urls.append(text)

        for key in (
            "image_url",
            "image_urls",
            "input_image",
            "input_images",
            "reference_image",
            "reference_images",
        ):
            value = normalized.get(key)
            if isinstance(value, list):
                for item in value:
                    _append_image_url(item)
            else:
                _append_image_url(value)

        if not image_urls:
            return normalized

        content: list[dict[str, Any]] = []
        if prompt:
            content.append({"type": "text", "text": prompt})
        for image_url in image_urls[:6]:
            content.append({"type": "image_url", "image_url": {"url": image_url}})
        normalized["messages"] = [{"role": "user", "content": content}]
        return normalized

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

    @router.post("/v1/images/generations")
    def openai_generate(data: dict, request: Request):
        require_service_api_key(request)

        prompt = str(data.get("prompt") or "").strip()
        normalized_data = _normalize_image_request_data(data, prompt)
        if not prompt:
            prompt = extract_prompt_from_messages(normalized_data.get("messages") or [])
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

        model_id = normalized_data.get("model")
        if str(model_id or "").strip() in video_model_catalog:
            return _json_response(
                status_code=400,
                content={
                    "error": {
                        "message": "Use /v1/video/generations or /v1/chat/completions for video generation",
                        "type": "invalid_request_error",
                    }
                },
                request=request,
            )
        ratio, output_resolution, resolved_model_id = resolve_ratio_and_resolution(
            normalized_data, model_id
        )
        model_conf = resolve_model(resolved_model_id)

        try:
            input_images = load_input_images(normalized_data.get("messages") or [])
            set_request_task_progress(
                request, task_status="IN_PROGRESS", task_progress=0.0
            )

            def _run_once(token: str):
                source_image_ids: list[str] = []
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

                imgbed_upload_enabled = bool(use_imgbed_upload())
                direct_result_url = bool(use_upstream_result_url()) or imgbed_upload_enabled
                job_id = uuid.uuid4().hex
                out_path = generated_dir / f"{job_id}.png"
                old_size = 0
                if not direct_result_url:
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
                    source_image_ids=source_image_ids,
                    timeout=client.generate_timeout,
                    out_path=None if direct_result_url else out_path,
                    progress_cb=_image_progress_cb,
                    return_upstream_url=direct_result_url,
                )
                upstream_image_url = _extract_upstream_asset_url(meta, "image")
                if imgbed_upload_enabled:
                    if not upstream_image_url:
                        raise HTTPException(
                            status_code=502,
                            detail="upstream result url missing",
                        )
                    image_url = upload_generated_asset_to_imgbed(
                        upstream_image_url,
                        filename=f"{job_id}.png",
                        mime_type="image/png",
                    )
                elif direct_result_url:
                    image_url = upstream_image_url
                    if not image_url:
                        raise HTTPException(
                            status_code=502,
                            detail="upstream result url missing",
                        )
                else:
                    if image_bytes is not None:
                        out_path.write_bytes(image_bytes)
                    new_size = int(out_path.stat().st_size) if out_path.exists() else 0
                    on_generated_file_written(out_path, old_size, new_size)
                    image_url = public_image_url(request, job_id)
                set_request_preview(request, image_url, kind="image")
                return {
                    "created": int(time.time()),
                    "model": resolved_model_id,
                    "data": [{"url": image_url}],
                }

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
                    imgbed_upload_enabled = bool(use_imgbed_upload())
                    direct_result_url = bool(use_upstream_result_url()) or imgbed_upload_enabled
                    out_path = generated_dir / f"{job_id}.png"
                    old_size = 0
                    if not direct_result_url:
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
                        out_path=None if direct_result_url else out_path,
                        return_upstream_url=direct_result_url,
                    )
                    upstream_image_url = _extract_upstream_asset_url(meta, "image")
                    if imgbed_upload_enabled:
                        if not upstream_image_url:
                            raise RuntimeError("upstream result url missing")
                        image_url = upload_generated_asset_to_imgbed(
                            upstream_image_url,
                            filename=f"{job_id}.png",
                            mime_type="image/png",
                        )
                    elif direct_result_url:
                        image_url = upstream_image_url
                        if not image_url:
                            raise RuntimeError("upstream result url missing")
                    else:
                        if image_bytes is not None:
                            out_path.write_bytes(image_bytes)
                        new_size = (
                            int(out_path.stat().st_size) if out_path.exists() else 0
                        )
                        on_generated_file_written(out_path, old_size, new_size)
                        image_url = public_image_url(request, job_id)
                    progress = float(meta.get("progress") or 100.0)
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

        return {"task_id": job.id, "status": job.status}

    @router.get("/api/v1/generate/{task_id}")
    def get_job(task_id: str, request: Request):
        require_service_api_key(request)

        job = store.get(task_id)
        if not job:
            raise HTTPException(status_code=404, detail="task not found")
        return asdict(job)

    def _normalize_video_request_data(data: dict, prompt: str) -> dict:
        normalized = dict(data or {})
        messages = normalized.get("messages")
        if isinstance(messages, list) and messages:
            return normalized

        image_urls: list[str] = []
        seen_urls: set[str] = set()

        def _append_image_url(value: Any) -> None:
            raw_value = value
            if isinstance(raw_value, dict):
                raw_value = (
                    raw_value.get("url")
                    or raw_value.get("image_url")
                    or raw_value.get("src")
                )
            text = str(raw_value or "").strip()
            if not text or text in seen_urls:
                return
            seen_urls.add(text)
            image_urls.append(text)

        for key in (
            "image_url",
            "image_urls",
            "input_image",
            "input_images",
            "reference_image",
            "reference_images",
        ):
            value = normalized.get(key)
            if isinstance(value, list):
                for item in value:
                    _append_image_url(item)
            else:
                _append_image_url(value)

        if not image_urls:
            return normalized

        content: list[dict[str, Any]] = []
        if prompt:
            content.append({"type": "text", "text": prompt})
        for image_url in image_urls[:6]:
            content.append(
                {"type": "image_url", "image_url": {"url": image_url}}
            )
        normalized["messages"] = [{"role": "user", "content": content}]
        return normalized

    def _wants_async_video_generation(data: dict) -> bool:
        for key in ("async", "async_mode", "background"):
            value = (data or {}).get(key)
            if isinstance(value, bool):
                if value:
                    return True
                continue
            if isinstance(value, (int, float)):
                if value != 0:
                    return True
                continue
            if isinstance(value, str):
                if value.strip().lower() in {"1", "true", "yes", "y", "on"}:
                    return True
        return False

    def _format_video_generation_job(job) -> dict:
        status = str(getattr(job, "status", "") or "queued").strip().lower()
        public_status = "completed" if status == "succeeded" else status
        video_url = str(getattr(job, "image_url", "") or "").strip()
        payload = {
            "id": f"vidgen-{str(job.id)[:24]}",
            "object": "video.generation",
            "created": int(float(getattr(job, "created_at", 0) or time.time())),
            "model": getattr(job, "model", None),
            "status": public_status,
            "task_id": job.id,
            "progress": float(getattr(job, "progress", 0.0) or 0.0),
        }
        if video_url:
            payload["url"] = video_url
            payload["video_url"] = video_url
            payload["data"] = [{"url": video_url}]
        error = str(getattr(job, "error", "") or "").strip()
        if error:
            payload["error"] = error
        return payload

    def _create_async_video_generation(data: dict, request: Request, prompt: str):
        normalized_data = _normalize_video_request_data(data, prompt)
        model_id = str(normalized_data.get("model") or "").strip()
        if not model_id:
            return _json_response(
                status_code=400,
                content={
                    "error": {
                        "message": "model is required",
                        "type": "invalid_request_error",
                    }
                },
                request=request,
            )
        if _looks_like_video_model_id(model_id) and model_id not in video_model_catalog:
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
        if video_conf is None:
            return _json_response(
                status_code=400,
                content={
                    "error": {
                        "message": "Only video models are supported on /v1/video/generations",
                        "type": "invalid_request_error",
                    }
                },
                request=request,
            )

        resolved_video_conf = _resolve_video_request_config(
            model_id, normalized_data, video_conf or {}
        )
        resolved_model_id = str(
            resolved_video_conf.get("resolved_model_id") or model_id
        )
        ratio = str(resolved_video_conf.get("aspect_ratio") or "9:16")
        duration = int(resolved_video_conf["duration"])
        video_resolution = str(resolved_video_conf.get("resolution") or "720p")
        video_engine = str(resolved_video_conf.get("engine") or "sora2")
        generate_audio = True
        negative_prompt = ""
        video_reference_mode = str(
            resolved_video_conf.get("reference_mode") or "frame"
        )
        resolved_video_options = resolve_video_options(normalized_data)
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
        video_locale, timeline_events, video_audio = _resolve_sora_video_extras(
            normalized_data
        )

        job = store.create(
            prompt=prompt,
            aspect_ratio=ratio,
            model=resolved_model_id,
            kind="video",
        )

        def runner(job_id: str) -> None:
            store.update(job_id, status="running", progress=1.0)
            max_attempts = client.retry_max_attempts if client.retry_enabled else 1
            max_attempts = max(1, int(max_attempts))
            last_error = "No active tokens available in the pool"
            try:
                input_images = load_input_images(normalized_data.get("messages") or [])
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
                    raise RuntimeError(
                        f"video model supports at most {max_video_inputs} input image(s)"
                    )
            except Exception as exc:
                store.update(job_id, status="failed", progress=0.0, error=str(exc))
                return

            for attempt in range(1, max_attempts + 1):
                token = token_manager.get_available(
                    strategy=client.token_rotation_strategy
                )
                if not token:
                    break

                try:
                    source_image_ids: list[str] = []
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
                        progress = update.get("task_progress")
                        try:
                            progress_value = float(progress)
                        except Exception:
                            progress_value = None
                        patch = {"status": "running"}
                        if progress_value is not None:
                            patch["progress"] = max(1.0, min(progress_value, 99.0))
                        error_text = str(update.get("error") or "").strip()
                        if error_text:
                            patch["error"] = error_text
                        store.update(job_id, **patch)

                    imgbed_upload_enabled = bool(use_imgbed_upload())
                    direct_result_url = (
                        bool(use_upstream_result_url()) or imgbed_upload_enabled
                    )
                    tmp_path = generated_dir / f"{job_id}.video.tmp"
                    old_size = 0
                    if not direct_result_url:
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
                        out_path=None if direct_result_url else tmp_path,
                        progress_cb=_video_progress_cb,
                        return_upstream_url=direct_result_url,
                    )
                    upstream_video_url = _extract_upstream_asset_url(
                        video_meta, "video"
                    )
                    video_ext = video_ext_from_meta(video_meta)
                    if imgbed_upload_enabled:
                        if not upstream_video_url:
                            raise RuntimeError("upstream result url missing")
                        video_url = upload_generated_asset_to_imgbed(
                            upstream_video_url,
                            filename=f"{job_id}.{video_ext}",
                            mime_type=_video_mime_type(video_ext),
                        )
                    elif direct_result_url:
                        video_url = upstream_video_url
                        if not video_url:
                            raise RuntimeError("upstream result url missing")
                    else:
                        filename = f"{job_id}.{video_ext}"
                        out_path = generated_dir / filename
                        if video_bytes is not None:
                            out_path.write_bytes(video_bytes)
                        elif tmp_path.exists():
                            tmp_path.replace(out_path)
                        new_size = int(out_path.stat().st_size) if out_path.exists() else 0
                        on_generated_file_written(out_path, old_size, new_size)
                        video_url = public_generated_url(request, filename)

                    store.update(
                        job_id,
                        status="succeeded",
                        progress=100.0,
                        image_url=video_url,
                        error=None,
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
        return _json_response(
            status_code=202,
            content=_format_video_generation_job(job),
            request=request,
        )

    def _handle_video_generation_request(
        data: dict,
        request: Request,
        *,
        prompt: str,
        response_mode: str,
    ):
        route_path = (
            "/v1/video/generations"
            if response_mode == "video"
            else "/v1/chat/completions"
        )
        operation_name = (
            "video.generations" if response_mode == "video" else "chat.completions"
        )
        normalized_data = _normalize_video_request_data(data, prompt)
        model_id = str(normalized_data.get("model") or "").strip()
        if not model_id:
            return _json_response(
                status_code=400,
                content={
                    "error": {
                        "message": "model is required",
                        "type": "invalid_request_error",
                    }
                },
                request=request,
            )
        if _looks_like_video_model_id(model_id) and model_id not in video_model_catalog:
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
        if video_conf is None:
            return _json_response(
                status_code=400,
                content={
                    "error": {
                        "message": f"Only video models are supported on {route_path}",
                        "type": "invalid_request_error",
                    }
                },
                request=request,
            )
        if response_mode == "video" and bool(normalized_data.get("stream", False)):
            return _json_response(
                status_code=400,
                content={
                    "error": {
                        "message": "stream is not supported on /v1/video/generations",
                        "type": "invalid_request_error",
                    }
                },
                request=request,
            )

        resolved_video_conf = _resolve_video_request_config(
            model_id, normalized_data, video_conf or {}
        )
        resolved_model_id = str(
            resolved_video_conf.get("resolved_model_id") or model_id
        )
        ratio = str(resolved_video_conf.get("aspect_ratio") or "9:16")
        duration = int(resolved_video_conf["duration"])
        video_resolution = str(resolved_video_conf.get("resolution") or "720p")
        video_engine = str(resolved_video_conf.get("engine") or "sora2")
        generate_audio = True
        negative_prompt = ""
        video_locale = "en-US"
        timeline_events = None
        video_audio = None
        video_reference_mode = str(
            resolved_video_conf.get("reference_mode") or "frame"
        )
        resolved_video_options = resolve_video_options(normalized_data)
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
        video_locale, timeline_events, video_audio = _resolve_sora_video_extras(
            normalized_data
        )

        try:
            input_images = load_input_images(normalized_data.get("messages") or [])
            set_request_task_progress(
                request, task_status="IN_PROGRESS", task_progress=0.0
            )

            def _run_once(token: str):
                source_image_ids: list[str] = []
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

                imgbed_upload_enabled = bool(use_imgbed_upload())
                direct_result_url = (
                    bool(use_upstream_result_url()) or imgbed_upload_enabled
                )
                task_id = uuid.uuid4().hex
                tmp_path = generated_dir / f"{task_id}.video.tmp"
                old_size = 0
                if not direct_result_url:
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
                    out_path=None if direct_result_url else tmp_path,
                    progress_cb=_video_progress_cb,
                    return_upstream_url=direct_result_url,
                )
                upstream_video_url = _extract_upstream_asset_url(video_meta, "video")
                video_ext = video_ext_from_meta(video_meta)
                if imgbed_upload_enabled:
                    if not upstream_video_url:
                        raise HTTPException(
                            status_code=502,
                            detail="upstream result url missing",
                        )
                    video_url = upload_generated_asset_to_imgbed(
                        upstream_video_url,
                        filename=f"{task_id}.{video_ext}",
                        mime_type=_video_mime_type(video_ext),
                    )
                elif direct_result_url:
                    video_url = upstream_video_url
                    if not video_url:
                        raise HTTPException(
                            status_code=502,
                            detail="upstream result url missing",
                        )
                else:
                    filename = f"{task_id}.{video_ext}"
                    out_path = generated_dir / filename
                    if video_bytes is not None:
                        out_path.write_bytes(video_bytes)
                    elif tmp_path.exists():
                        tmp_path.replace(out_path)
                    new_size = int(out_path.stat().st_size) if out_path.exists() else 0
                    on_generated_file_written(out_path, old_size, new_size)
                    video_url = public_generated_url(request, filename)

                set_request_preview(request, video_url, kind="video")
                created_ts = int(time.time())
                return {
                    "id": f"vidgen-{task_id[:24]}",
                    "object": "video.generation",
                    "created": created_ts,
                    "model": resolved_model_id,
                    "status": "completed",
                    "task_id": task_id,
                    "url": video_url,
                    "video_url": video_url,
                    "data": [{"url": video_url}],
                }

            result = run_with_token_retries(
                request=request,
                operation_name=operation_name,
                run_once=_run_once,
            )
            if response_mode == "video":
                return result

            video_url = str(result.get("url") or "").strip()
            response_payload = {
                "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
                "object": "chat.completion",
                "created": int(result.get("created") or time.time()),
                "model": str(result.get("model") or resolved_model_id),
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": f"```html\n<video src='{video_url}' controls></video>\n```",
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
            if bool(normalized_data.get("stream", False)):
                return StreamingResponse(
                    sse_chat_stream(response_payload),
                    media_type="text/event-stream",
                )
            return response_payload
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
                "Unhandled error in %s log_id=%s model=%s resolved_model=%s is_video_model=%s",
                route_path,
                getattr(request.state, "log_id", ""),
                model_id,
                resolved_model_id,
                True,
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

    @router.post("/v1/video/generations")
    def video_generations(data: dict, request: Request):
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
        _validate_prompt_length(prompt)
        if _wants_async_video_generation(data):
            return _create_async_video_generation(data, request, prompt)
        return _handle_video_generation_request(
            data,
            request,
            prompt=prompt,
            response_mode="video",
        )

    @router.get("/v1/video/generations/{task_id}")
    def get_video_generation(task_id: str, request: Request):
        require_service_api_key(request)

        job = store.get(task_id)
        if not job or str(getattr(job, "kind", "") or "") != "video":
            raise HTTPException(status_code=404, detail="video generation not found")
        return _format_video_generation_job(job)

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
        if _looks_like_video_model_id(model_id) and model_id not in video_model_catalog:
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
        if model_id in video_model_catalog:
            return _handle_video_generation_request(
                data,
                request,
                prompt=prompt,
                response_mode="chat",
            )

        _validate_prompt_length(prompt)
        ratio, output_resolution, resolved_model_id = resolve_ratio_and_resolution(
            data, model_id or None
        )
        image_model_conf = resolve_model(resolved_model_id)

        try:
            input_images = load_input_images(data.get("messages") or [])
            set_request_task_progress(
                request, task_status="IN_PROGRESS", task_progress=0.0
            )

            def _run_once(token: str):
                source_image_ids: list[str] = []
                image_url = ""
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

                imgbed_upload_enabled = bool(use_imgbed_upload())
                direct_result_url = bool(use_upstream_result_url()) or imgbed_upload_enabled
                job_id = uuid.uuid4().hex
                out_path = generated_dir / f"{job_id}.png"
                old_size = 0
                if not direct_result_url:
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
                        image_model_conf.get("upstream_model_id") or "gemini-flash"
                    ),
                    upstream_model_version=str(
                        image_model_conf.get("upstream_model_version")
                        or "nano-banana-2"
                    ),
                    source_image_ids=source_image_ids,
                    timeout=client.generate_timeout,
                    out_path=None if direct_result_url else out_path,
                    progress_cb=_image_progress_cb,
                    return_upstream_url=direct_result_url,
                )
                upstream_image_url = _extract_upstream_asset_url(meta, "image")
                if imgbed_upload_enabled:
                    if not upstream_image_url:
                        raise HTTPException(
                            status_code=502,
                            detail="upstream result url missing",
                        )
                    image_url = upload_generated_asset_to_imgbed(
                        upstream_image_url,
                        filename=f"{job_id}.png",
                        mime_type="image/png",
                    )
                elif direct_result_url:
                    image_url = upstream_image_url
                    if not image_url:
                        raise HTTPException(
                            status_code=502,
                            detail="upstream result url missing",
                        )
                else:
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
                return response_payload

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
                "Unhandled error in /v1/chat/completions log_id=%s model=%s resolved_model=%s",
                getattr(request.state, "log_id", ""),
                model_id,
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

    return router
