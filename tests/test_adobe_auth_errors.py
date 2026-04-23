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
