from __future__ import annotations

SUPPORTED_RATIOS = {"1:1", "16:9", "9:16", "4:3", "3:4"}
RATIO_SUFFIX_MAP = {
    "1:1": "1x1",
    "16:9": "16x9",
    "9:16": "9x16",
    "4:3": "4x3",
    "3:4": "3x4",
}

MODEL_CATALOG: dict[str, dict] = {}


def _register_image_model(
    model_id: str,
    *,
    upstream_model_id: str,
    upstream_model_version: str,
    family_label: str,
    fixed_output_resolution: str | None = None,
) -> None:
    resolution_options = (
        [fixed_output_resolution]
        if fixed_output_resolution
        else ["1K", "2K"]
    )
    MODEL_CATALOG[model_id] = {
        "upstream_model": "google:firefly:colligo:nano-banana-pro",
        "upstream_model_id": upstream_model_id,
        "upstream_model_version": upstream_model_version,
        "output_resolution": fixed_output_resolution or "2K",
        "output_resolution_options": resolution_options,
        "aspect_ratio": "16:9",
        "aspect_ratio_options": ["1:1", "16:9", "9:16", "4:3", "3:4"],
        "description": (
            f"{family_label} 4K image model (set aspect_ratio in request)"
            if fixed_output_resolution == "4K"
            else f"{family_label} image model (set output_resolution/aspect_ratio in request)"
        ),
        "allow_request_overrides": True,
    }

    for res in ("1k", "2k", "4k"):
        for ratio, suffix in RATIO_SUFFIX_MAP.items():
            for alias_id in (f"{model_id}-{res}-{suffix}", f"firefly-{model_id}-{res}-{suffix}"):
                MODEL_CATALOG[alias_id] = {
                    "upstream_model": "google:firefly:colligo:nano-banana-pro",
                    "upstream_model_id": upstream_model_id,
                    "upstream_model_version": upstream_model_version,
                    "output_resolution": res.upper(),
                    "aspect_ratio": ratio,
                    "description": f"{family_label} ({res.upper()} {ratio})",
                    "canonical_model": model_id,
                    "hidden": True,
                    "allow_request_overrides": False,
                }


def _register_image_family_alias(alias_id: str, canonical_model: str) -> None:
    base = dict(MODEL_CATALOG[canonical_model])
    base.update(
        {
            "canonical_model": canonical_model,
            "hidden": True,
            "allow_request_overrides": True,
        }
    )
    MODEL_CATALOG[alias_id] = base


_register_image_model(
    "nano-banana-pro",
    upstream_model_id="gemini-flash",
    upstream_model_version="nano-banana-2",
    family_label="Nano Banana Pro",
)
_register_image_model(
    "nano-banana-pro-4k",
    upstream_model_id="gemini-flash",
    upstream_model_version="nano-banana-2",
    family_label="Nano Banana Pro",
    fixed_output_resolution="4K",
)
_register_image_model(
    "nano-banana",
    upstream_model_id="gemini-flash",
    upstream_model_version="nano-banana-2",
    family_label="Nano Banana",
)
_register_image_model(
    "nano-banana-4k",
    upstream_model_id="gemini-flash",
    upstream_model_version="nano-banana-2",
    family_label="Nano Banana",
    fixed_output_resolution="4K",
)
_register_image_model(
    "nano-banana2",
    upstream_model_id="gemini-flash",
    upstream_model_version="nano-banana-3",
    family_label="Nano Banana 2",
)
_register_image_model(
    "nano-banana2-4k",
    upstream_model_id="gemini-flash",
    upstream_model_version="nano-banana-3",
    family_label="Nano Banana 2",
    fixed_output_resolution="4K",
)

for canonical_id in (
    "nano-banana",
    "nano-banana-4k",
    "nano-banana-pro",
    "nano-banana-pro-4k",
    "nano-banana2",
    "nano-banana2-4k",
):
    _register_image_family_alias(f"firefly-{canonical_id}", canonical_id)

DEFAULT_MODEL_ID = "nano-banana-pro"

VIDEO_MODEL_CATALOG: dict[str, dict] = {}


def _register_video_model(
    model_id: str,
    *,
    description: str,
    engine: str = "sora2",
    upstream_model: str | None = None,
    duration: int = 8,
    duration_options: tuple[int, ...] = (),
    aspect_ratio: str = "16:9",
    aspect_ratio_options: tuple[str, ...] = (),
    resolution: str | None = None,
    resolution_options: tuple[str, ...] = (),
    reference_mode: str = "frame",
    reference_mode_options: tuple[str, ...] = (),
) -> None:
    VIDEO_MODEL_CATALOG[model_id] = {
        "description": description,
        "engine": engine,
        "upstream_model": upstream_model,
        "duration": duration,
        "duration_options": list(duration_options or (duration,)),
        "aspect_ratio": aspect_ratio,
        "aspect_ratio_options": list(aspect_ratio_options or (aspect_ratio,)),
        "resolution": resolution,
        "resolution_options": list(resolution_options),
        "reference_mode": reference_mode,
        "reference_mode_options": list(reference_mode_options or (reference_mode,)),
        "allow_request_overrides": True,
    }


def _register_video_family_alias(alias_id: str, canonical_model: str) -> None:
    base = dict(VIDEO_MODEL_CATALOG[canonical_model])
    base.update(
        {
            "canonical_model": canonical_model,
            "hidden": True,
            "allow_request_overrides": True,
        }
    )
    VIDEO_MODEL_CATALOG[alias_id] = base


def _register_video_alias(
    alias_id: str,
    *,
    canonical_model: str,
    duration: int,
    aspect_ratio: str,
    resolution: str | None = None,
    reference_mode: str = "frame",
    description: str,
) -> None:
    base = dict(VIDEO_MODEL_CATALOG[canonical_model])
    base.update(
        {
            "canonical_model": canonical_model,
            "description": description,
            "duration": duration,
            "aspect_ratio": aspect_ratio,
            "resolution": resolution,
            "reference_mode": reference_mode,
            "hidden": True,
            "allow_request_overrides": False,
        }
    )
    VIDEO_MODEL_CATALOG[alias_id] = base


_register_video_model(
    "sora2",
    description="Sora2 video model (set duration/aspect_ratio in request)",
    engine="sora2",
    upstream_model="openai:firefly:colligo:sora2",
    duration=8,
    duration_options=(4, 8, 12),
    aspect_ratio="16:9",
    aspect_ratio_options=("16:9", "9:16"),
)

_register_video_model(
    "sora2-pro",
    description="Sora2 Pro video model (set duration/aspect_ratio in request)",
    engine="sora2",
    upstream_model="openai:firefly:colligo:sora2-pro",
    duration=8,
    duration_options=(4, 8, 12),
    aspect_ratio="16:9",
    aspect_ratio_options=("16:9", "9:16"),
)

_register_video_model(
    "veo31",
    description="Veo31 video model (set duration/aspect_ratio/resolution/reference_mode in request)",
    engine="veo31-standard",
    upstream_model="google:firefly:colligo:veo31",
    duration=4,
    duration_options=(4, 6, 8),
    aspect_ratio="16:9",
    aspect_ratio_options=("16:9", "9:16"),
    resolution="720p",
    resolution_options=("720p", "1080p"),
    reference_mode="frame",
    reference_mode_options=("frame", "image"),
)

_register_video_model(
    "veo31-ref",
    description="Veo31 Ref video model (set duration/aspect_ratio/resolution in request)",
    engine="veo31-standard",
    upstream_model="google:firefly:colligo:veo31",
    duration=4,
    duration_options=(4, 6, 8),
    aspect_ratio="16:9",
    aspect_ratio_options=("16:9", "9:16"),
    resolution="720p",
    resolution_options=("720p", "1080p"),
    reference_mode="image",
    reference_mode_options=("image",),
)

_register_video_model(
    "veo31-fast",
    description="Veo31 Fast video model (set duration/aspect_ratio/resolution in request)",
    engine="veo31-fast",
    upstream_model="google:firefly:colligo:veo31-fast",
    duration=4,
    duration_options=(4, 6, 8),
    aspect_ratio="16:9",
    aspect_ratio_options=("16:9", "9:16"),
    resolution="720p",
    resolution_options=("720p", "1080p"),
    reference_mode="frame",
)

for canonical_id in ("sora2", "sora2-pro", "veo31", "veo31-ref", "veo31-fast"):
    _register_video_family_alias(f"firefly-{canonical_id}", canonical_id)

for dur in (4, 8, 12):
    for ratio in ("9:16", "16:9"):
        for alias_id in (
            f"sora2-{dur}s-{RATIO_SUFFIX_MAP[ratio]}",
            f"firefly-sora2-{dur}s-{RATIO_SUFFIX_MAP[ratio]}",
        ):
            _register_video_alias(
                alias_id,
                canonical_model="sora2",
                duration=dur,
                aspect_ratio=ratio,
                description=f"Sora2 video model ({dur}s {ratio})",
            )

for dur in (4, 8, 12):
    for ratio in ("9:16", "16:9"):
        for alias_id in (
            f"sora2-pro-{dur}s-{RATIO_SUFFIX_MAP[ratio]}",
            f"firefly-sora2-pro-{dur}s-{RATIO_SUFFIX_MAP[ratio]}",
        ):
            _register_video_alias(
                alias_id,
                canonical_model="sora2-pro",
                duration=dur,
                aspect_ratio=ratio,
                description=f"Sora2 Pro video model ({dur}s {ratio})",
            )

for dur in (4, 6, 8):
    for ratio in ("16:9", "9:16"):
        for res in ("1080p", "720p"):
            for alias_id in (
                f"veo31-{dur}s-{RATIO_SUFFIX_MAP[ratio]}-{res}",
                f"firefly-veo31-{dur}s-{RATIO_SUFFIX_MAP[ratio]}-{res}",
            ):
                _register_video_alias(
                    alias_id,
                    canonical_model="veo31",
                    duration=dur,
                    aspect_ratio=ratio,
                    resolution=res,
                    description=f"Veo31 video model ({dur}s {ratio} {res})",
                )

for dur in (4, 6, 8):
    for ratio in ("16:9", "9:16"):
        for res in ("1080p", "720p"):
            for alias_id in (
                f"veo31-ref-{dur}s-{RATIO_SUFFIX_MAP[ratio]}-{res}",
                f"firefly-veo31-ref-{dur}s-{RATIO_SUFFIX_MAP[ratio]}-{res}",
            ):
                _register_video_alias(
                    alias_id,
                    canonical_model="veo31-ref",
                    duration=dur,
                    aspect_ratio=ratio,
                    resolution=res,
                    reference_mode="image",
                    description=f"Veo31 Ref video model ({dur}s {ratio} {res})",
                )

for dur in (4, 6, 8):
    for ratio in ("16:9", "9:16"):
        for res in ("1080p", "720p"):
            for alias_id in (
                f"veo31-fast-{dur}s-{RATIO_SUFFIX_MAP[ratio]}-{res}",
                f"firefly-veo31-fast-{dur}s-{RATIO_SUFFIX_MAP[ratio]}-{res}",
            ):
                _register_video_alias(
                    alias_id,
                    canonical_model="veo31-fast",
                    duration=dur,
                    aspect_ratio=ratio,
                    resolution=res,
                    description=f"Veo31 Fast video model ({dur}s {ratio} {res})",
                )
