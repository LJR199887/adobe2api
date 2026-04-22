from __future__ import annotations

import time
from typing import Optional


def size_from_ratio(ratio: str, output_resolution: str = "2K") -> dict:
    level = (output_resolution or "2K").upper()
    if level == "1K":
        ratio_map = {
            "1:1": {"width": 1024, "height": 1024},
            "16:9": {"width": 1360, "height": 768},
            "9:16": {"width": 768, "height": 1360},
            "4:3": {"width": 1152, "height": 864},
            "3:4": {"width": 864, "height": 1152},
            "3:2": {"width": 1536, "height": 1024},
            "2:3": {"width": 1024, "height": 1536},
        }
    elif level == "4K":
        ratio_map = {
            "1:1": {"width": 4096, "height": 4096},
            "16:9": {"width": 5504, "height": 3072},
            "9:16": {"width": 3072, "height": 5504},
            "4:3": {"width": 4096, "height": 3072},
            "3:4": {"width": 3072, "height": 4096},
            "3:2": {"width": 4608, "height": 3072},
            "2:3": {"width": 3072, "height": 4608},
        }
    else:
        ratio_map = {
            "1:1": {"width": 2048, "height": 2048},
            "16:9": {"width": 2752, "height": 1536},
            "9:16": {"width": 1536, "height": 2752},
            "4:3": {"width": 2048, "height": 1536},
            "3:4": {"width": 1536, "height": 2048},
            "3:2": {"width": 2304, "height": 1536},
            "2:3": {"width": 1536, "height": 2304},
        }
    return ratio_map.get(ratio, ratio_map["16:9"])


def build_image_payload_candidates(
    *,
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
) -> list[dict]:
    def _merge_model_specific_payload(default_payload: dict) -> dict:
        merged = dict(default_payload)
        extra = dict(model_specific_payload or {})
        extra_parameters = extra.pop("parameters", None)
        merged.update(extra)
        if isinstance(extra_parameters, dict):
            parameters = dict(merged.get("parameters") or {})
            parameters.update(extra_parameters)
            merged["parameters"] = parameters
        return merged

    if payload_style == "gpt_image2":
        metadata = dict(
            generation_metadata
            or {"module": "text2image", "submodule": "ff-image-generate"}
        )
        payload = {
            "modelId": upstream_model_id,
            "modelVersion": upstream_model_version,
            "n": 1,
            "prompt": prompt,
            "seeds": [int(time.time()) % 999999],
            "referenceBlobs": [],
            "output": {"storeInputs": True},
            "modelSpecificPayload": dict(model_specific_payload or {}),
            "generationMetadata": metadata,
        }
        if source_image_ids:
            payload["referenceBlobs"] = [
                {"id": img_id, "usage": "subject"} for img_id in source_image_ids
            ]
            payload["modelSpecificPayload"].setdefault("size", "auto")
        else:
            payload["size"] = size_from_ratio(aspect_ratio, output_resolution)
        if generation_settings:
            payload["generationSettings"] = dict(generation_settings)
        return [payload]

    metadata = dict(
        generation_metadata or {"module": "text2image", "submodule": "ff-image-generate"}
    )
    base_payload = {
        "modelId": upstream_model_id,
        "modelVersion": upstream_model_version,
        "n": 1,
        "prompt": prompt,
        "size": size_from_ratio(aspect_ratio, output_resolution),
        "seeds": [int(time.time()) % 999999],
        "groundSearch": False,
        "output": {"storeInputs": True},
        "generationMetadata": metadata,
        "modelSpecificPayload": _merge_model_specific_payload(
            {"parameters": {"addWatermark": False}}
        ),
    }
    if generation_settings:
        base_payload["generationSettings"] = dict(generation_settings)

    if not source_image_ids:
        base_payload["referenceBlobs"] = []
        return [base_payload]

    candidates: list[dict] = []
    edited = dict(base_payload)
    edited_metadata = dict(metadata)
    edited_metadata["module"] = "image2image"
    edited["generationMetadata"] = edited_metadata

    c1 = dict(edited)
    c1["referenceBlobs"] = [
        {"id": img_id, "usage": "general"} for img_id in source_image_ids
    ]
    candidates.append(c1)

    c4 = dict(edited)
    c4["referenceBlobs"] = []
    c4["imagePrompt"] = {"referenceImage": source_image_ids[0]}
    candidates.append(c4)

    c5 = dict(edited)
    c5["referenceBlobs"] = []
    c5["imagePrompt"] = {"referenceImage": {"id": source_image_ids[0]}}
    candidates.append(c5)

    return candidates
