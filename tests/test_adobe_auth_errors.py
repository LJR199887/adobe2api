import base64
import hashlib
import json

import pytest

from core.adobe_client import AdobeClient, QuotaExhaustedError


class FakeResponse:
    def __init__(self, status_code, headers=None, text="", json_data=None):
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text
        self._json_data = json_data or {}

    def json(self):
        return self._json_data


def make_jwt(payload):
    header = {"alg": "none", "typ": "JWT"}

    def encode(obj):
        raw = json.dumps(obj, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    return f"{encode(header)}.{encode(payload)}."


def test_submit_headers_include_nonce_from_user_id_and_prompt():
    client = AdobeClient()
    token = make_jwt({"user_id": "user-123"})
    prompt = "hello world"

    headers = client._submit_headers(token, prompt=prompt)

    assert headers["x-nonce"] == hashlib.sha256(
        f"user-123-{prompt}".encode("utf-8")
    ).hexdigest()
    assert headers["x-api-key"] == client.api_key


def test_submit_headers_include_fresh_arp_session_id():
    client = AdobeClient()

    first = client._submit_headers("token")
    second = client._submit_headers_minimal("token")

    for headers in (first, second):
        decoded = json.loads(base64.b64decode(headers["x-arp-session-id"]))
        assert decoded["sid"]
        assert decoded["ftr"].endswith("_dUAL43-mnts-ants-d4_31ck__tt")
    assert first["x-arp-session-id"] != second["x-arp-session-id"]


def test_poll_headers_include_api_key_and_browser_fetch_headers():
    client = AdobeClient()

    headers = client._poll_headers("token")

    assert headers["x-api-key"] == client.api_key
    assert headers["sec-ch-ua"] == client.sec_ch_ua
    assert headers["sec-fetch-site"] == "cross-site"


def test_image_poll_taste_exhausted_raises_quota(monkeypatch):
    client = AdobeClient()

    def fake_post_json(url, headers, payload):
        return FakeResponse(
            200,
            headers={"x-override-status-link": "https://example.test/image/status/1"},
            json_data={},
        )

    def fake_get(url, headers, timeout=60, proxy_kind="basic"):
        return FakeResponse(
            401,
            headers={"x-access-error": "taste_exhausted"},
            text="quota exhausted",
        )

    monkeypatch.setattr(client, "_post_json", fake_post_json)
    monkeypatch.setattr(client, "_get", fake_get)

    with pytest.raises(QuotaExhaustedError):
        client.generate(token="token", prompt="hello world")


def test_video_poll_taste_exhausted_raises_quota(monkeypatch):
    client = AdobeClient()

    def fake_post_json(url, headers, payload):
        return FakeResponse(
            200,
            headers={"x-override-status-link": "https://example.test/video/status/1"},
            json_data={},
        )

    def fake_get(url, headers, timeout=60, proxy_kind="basic"):
        return FakeResponse(
            401,
            headers={"x-access-error": "taste_exhausted"},
            text="quota exhausted",
        )

    monkeypatch.setattr(client, "_post_json", fake_post_json)
    monkeypatch.setattr(client, "_get", fake_get)

    with pytest.raises(QuotaExhaustedError):
        client.generate_video(
            token="token",
            video_conf={"engine": "sora2"},
            prompt="hello world",
        )


def test_poll_token_quota_exhausted_text_raises_quota(monkeypatch):
    client = AdobeClient()

    def fake_post_json(url, headers, payload):
        return FakeResponse(
            200,
            headers={"x-override-status-link": "https://example.test/image/status/1"},
            json_data={},
        )

    def fake_get(url, headers, timeout=60, proxy_kind="basic"):
        return FakeResponse(401, text="Token quota exhausted")

    monkeypatch.setattr(client, "_post_json", fake_post_json)
    monkeypatch.setattr(client, "_get", fake_get)

    with pytest.raises(QuotaExhaustedError):
        client.generate(token="token", prompt="hello world")
