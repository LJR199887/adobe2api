import requests

import app as app_module


class FakeResponse:
    status_code = 200
    content = b"image-bytes"
    headers = {"content-type": "image/png"}


def test_input_image_download_retries_twice_with_120_second_timeout(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        if len(calls) < 3:
            raise requests.exceptions.ReadTimeout("slow source")
        return FakeResponse()

    monkeypatch.setattr(app_module.requests, "get", fake_get)
    monkeypatch.setattr(app_module.time, "sleep", lambda _seconds: None)

    loaded = app_module._load_input_images(
        [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": "https://example.com/input.png"},
                    }
                ],
            }
        ]
    )

    assert loaded == [(b"image-bytes", "image/png")]
    assert len(calls) == 3
    assert all(kwargs["timeout"] == 120 for _, kwargs in calls)
