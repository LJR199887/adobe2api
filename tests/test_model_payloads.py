from core.adobe_client import AdobeClient
from core.models import (
    MODEL_CATALOG,
    VIDEO_MODEL_CATALOG,
    resolve_ratio_and_resolution,
)
from core.models.payloads import build_image_payload_candidates


def test_gpt_image2_catalog_entry_matches_upstream_request_shape():
    conf = MODEL_CATALOG["gpt-image2"]

    payload = build_image_payload_candidates(
        prompt="生成一张广州旅游攻略图",
        aspect_ratio="2:3",
        output_resolution="1K",
        upstream_model_id=conf["upstream_model_id"],
        upstream_model_version=conf["upstream_model_version"],
        payload_style=conf["payload_style"],
        generation_metadata=conf["generation_metadata"],
        generation_settings=conf["generation_settings"],
        model_specific_payload=conf["model_specific_payload"],
    )[0]

    assert payload["modelId"] == "gpt-image"
    assert payload["modelVersion"] == "2"
    assert payload["size"] == {"width": 1024, "height": 1536}
    assert payload["modelSpecificPayload"] == {}
    assert payload["generationMetadata"] == {
        "module": "text2image",
        "submodule": "ff-image-generate",
    }
    assert payload["generationSettings"] == {"detailLevel": 3}
    assert "groundSearch" not in payload
    assert "skipCai" not in payload


def test_gpt_image2_image_to_image_uses_top_level_size_from_ratio():
    conf = MODEL_CATALOG["gpt-image2"]
    source_ids = [
        "d69800be-273b-4808-99ce-6f3a7de5b070",
        "a778f3b7-ede2-4062-b75b-c1cbfb418d6c",
        "941c60e3-7d7c-47a4-890a-fe14c5f7278d",
    ]

    payload = build_image_payload_candidates(
        prompt="6张图片合在一起",
        aspect_ratio="2:3",
        output_resolution="1K",
        upstream_model_id=conf["upstream_model_id"],
        upstream_model_version=conf["upstream_model_version"],
        source_image_ids=source_ids,
        payload_style=conf["payload_style"],
        generation_metadata=conf["generation_metadata"],
        generation_settings=conf["generation_settings"],
        model_specific_payload=conf["model_specific_payload"],
    )[0]

    assert payload["modelId"] == "gpt-image"
    assert payload["modelVersion"] == "2"
    assert payload["size"] == {"width": 1024, "height": 1536}
    assert payload["referenceBlobs"] == [
        {"id": source_ids[0], "usage": "subject"},
        {"id": source_ids[1], "usage": "subject"},
        {"id": source_ids[2], "usage": "subject"},
    ]
    assert payload["modelSpecificPayload"] == {}
    assert payload["generationMetadata"] == {
        "module": "text2image",
        "submodule": "ff-image-generate",
    }
    assert payload["generationSettings"] == {"detailLevel": 3}


def test_gpt_image2_resolves_firefly_alias_and_2x3_size():
    ratio, output_resolution, resolved_model_id = resolve_ratio_and_resolution(
        {"model": "gpt-image2", "size": "1024x1536"},
        "gpt-image2",
    )

    assert ratio == "2:3"
    assert output_resolution == "1K"
    assert resolved_model_id == "gpt-image2"
    assert "firefly-gpt-image2" not in MODEL_CATALOG


def _build_kling_payload(model_id: str, resolution: str | None = None) -> dict:
    conf = VIDEO_MODEL_CATALOG[model_id]
    if resolution:
        conf = dict(conf)
        conf["resolution"] = resolution
    client = AdobeClient.__new__(AdobeClient)

    return client._build_video_payload(
        video_conf=conf,
        prompt="A cinematic city skyline at sunset",
        aspect_ratio="9:16",
        duration=15,
        generate_audio=True,
    )


def test_kling_video_catalog_matches_upstream_request_shape():
    conf = VIDEO_MODEL_CATALOG["kling"]
    payload = _build_kling_payload("kling")

    assert conf["max_input_images"] == 0
    assert conf["resolution_options"] == ["720p", "1080p"]
    assert VIDEO_MODEL_CATALOG["firefly-kling"]["canonical_model"] == "kling"
    assert payload["modelId"] == "kling"
    assert payload["modelVersion"] == "kling_v3_pro_t2v"
    assert payload["prompt"] == "A cinematic city skyline at sunset"
    assert payload["size"] == {"width": 1080, "height": 1920}
    assert payload["duration"] == 15
    assert payload["generateAudio"] is True
    assert payload["generationMetadata"] == {"module": "text2video"}
    assert payload["generationSettings"] == {"aspectRatio": "9:16"}
    assert payload["referenceBlobs"] == []
    assert "modelSpecificPayload" not in payload


def test_kling_video_720p_uses_portrait_720_size():
    payload = _build_kling_payload("kling", resolution="720p")

    assert payload["modelId"] == "kling"
    assert payload["modelVersion"] == "kling_v3_standard_t2v"
    assert payload["size"] == {"width": 720, "height": 1280}
    assert payload["generateAudio"] is False
    assert payload["generationSettings"] == {"aspectRatio": "9:16"}


def test_kling_omni_video_catalog_matches_upstream_request_shape():
    conf = VIDEO_MODEL_CATALOG["kling-omni"]
    payload = _build_kling_payload("kling-omni")

    assert conf["max_input_images"] == 0
    assert VIDEO_MODEL_CATALOG["firefly-kling-omni"]["canonical_model"] == "kling-omni"
    assert payload["modelId"] == "kling"
    assert payload["modelVersion"] == "kling_o3_pro_t2v"
    assert payload["prompt"] == "A cinematic city skyline at sunset"
    assert payload["size"] == {"width": 1080, "height": 1920}
    assert payload["duration"] == 15
    assert payload["generateAudio"] is True
    assert payload["generationMetadata"] == {"module": "text2video"}
    assert payload["generationSettings"] == {"aspectRatio": "9:16"}
    assert payload["referenceBlobs"] == []
    assert "modelSpecificPayload" not in payload
