from __future__ import annotations

from typing import Optional

from fastapi import HTTPException

from .catalog import DEFAULT_MODEL_ID, MODEL_CATALOG, SUPPORTED_RATIOS


def resolve_model(model_id: Optional[str]) -> dict:
    if not model_id:
        return MODEL_CATALOG[DEFAULT_MODEL_ID]
    if model_id not in MODEL_CATALOG:
        raise HTTPException(status_code=400, detail=f"Invalid model: {model_id}")
    return MODEL_CATALOG[model_id]


def ratio_from_size(size: str) -> str:
    mapping = {
        "1024x1024": "1:1",
        "1536x1536": "1:1",
        "2048x2048": "1:1",
        "1024x1792": "9:16",
        "1536x2752": "9:16",
        "1792x1024": "16:9",
        "2752x1536": "16:9",
        "2048x1536": "4:3",
        "1536x2048": "3:4",
        "1536x1024": "3:2",
        "1024x1536": "2:3",
    }
    return mapping.get(str(size or "").strip(), "1:1")


def _normalize_output_resolution(value: str) -> str:
    normalized = str(value or "").strip().upper()
    aliases = {
        "1K": "1K",
        "HD": "2K",
        "2K": "2K",
        "4K": "4K",
        "ULTRA": "4K",
    }
    return aliases.get(normalized, normalized or "2K")


def resolve_ratio_and_resolution(
    data: dict, model_id: Optional[str]
) -> tuple[str, str, str]:
    resolved_model_id = model_id or DEFAULT_MODEL_ID
    if resolved_model_id not in MODEL_CATALOG:
        resolved_model_id = DEFAULT_MODEL_ID
    model_conf = MODEL_CATALOG[resolved_model_id]

    if not model_conf.get("allow_request_overrides"):
        ratio = str(model_conf.get("aspect_ratio") or "1:1").strip()
        output_resolution = str(model_conf.get("output_resolution") or "2K").upper()
        return (
            ratio,
            output_resolution,
            str(model_conf.get("canonical_model") or resolved_model_id),
        )

    ratio = str(data.get("aspect_ratio") or "").strip() or ratio_from_size(
        data.get("size", "1024x1024")
    )
    allowed_ratios = [
        str(item).strip()
        for item in (model_conf.get("aspect_ratio_options") or [])
        if str(item).strip()
    ]
    if ratio not in SUPPORTED_RATIOS or (allowed_ratios and ratio not in allowed_ratios):
        ratio = str(model_conf.get("aspect_ratio") or "1:1").strip()

    output_resolution = _normalize_output_resolution(
        data.get("output_resolution") or model_conf.get("output_resolution") or "2K"
    )
    if not model_id:
        quality = str(data.get("quality", "2k")).lower()
        if quality in ("4k", "ultra"):
            output_resolution = "4K"
        elif quality in ("hd", "2k"):
            output_resolution = "2K"
        else:
            output_resolution = "1K"

    allowed_resolutions = [
        str(item).strip().upper()
        for item in (model_conf.get("output_resolution_options") or [])
        if str(item).strip()
    ]
    if allowed_resolutions and output_resolution not in allowed_resolutions:
        output_resolution = str(model_conf.get("output_resolution") or "2K").upper()

    return ratio, output_resolution, str(model_conf.get("canonical_model") or resolved_model_id)
