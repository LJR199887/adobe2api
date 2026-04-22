import json
import os
import re
import time
from pathlib import Path

import requests


BASE_DIR = Path(__file__).resolve().parent
CAPTURE_FILE = Path(r"D:\my_project\b69f19b4-5ab2-4f46-a944-9804ea9b4e98.json")


def extract_token_and_api_key(capture_path: Path):
    if not capture_path.exists():
        return None, None

    data = json.loads(capture_path.read_text(encoding="utf-8"))
    logs = data.get("logs", [])

    token = None
    api_key = None

    for item in logs:
        req = item.get("request") or {}
        headers = req.get("headers") or {}
        url = req.get("url") or ""

        # Prefer generation request headers
        if "firefly-3p.ff.adobe.io/v2/3p-images/generate-async" in url:
            for k, v in headers.items():
                lk = k.lower()
                if lk == "authorization" and isinstance(v, str) and v.startswith("Bearer "):
                    token = v.split(" ", 1)[1].strip()
                if lk == "x-api-key" and isinstance(v, str):
                    api_key = v.strip()
            if token and api_key:
                return token, api_key

    # Fallback: first bearer + first x-api-key
    bearer_re = re.compile(r"^Bearer\s+(.+)$")
    for item in logs:
        headers = ((item.get("request") or {}).get("headers") or {})
        for k, v in headers.items():
            if k.lower() == "authorization" and isinstance(v, str):
                m = bearer_re.match(v)
                if m:
                    token = token or m.group(1).strip()
            if k.lower() == "x-api-key" and isinstance(v, str):
                api_key = api_key or v.strip()
        if token and api_key:
            return token, api_key

    return token, api_key


def build_payload_candidates(prompt: str):
    model_payload = {
        "modelId": "gemini-flash",
        "modelVersion": "nano-banana-2",
        "n": 1,
        "prompt": prompt,
        "size": {"width": 2048, "height": 2048},
        "seeds": [134648],
        "referenceBlobs": [],
        "groundSearch": False,
        "output": {"storeInputs": True},
        "generationMetadata": {
            "module": "text2image",
            "submodule": "ff-image-generate",
        },
        "modelSpecificPayload": {
            "parameters": {"addWatermark": False},
        },
    }

    return [
        model_payload,
        {"input": {"firefly#model": model_payload}},
    ]


def submit_job(token: str, api_key: str, prompt: str):
    url = "https://firefly-3p.ff.adobe.io/v2/3p-images/generate-async"
    headers = {
        "Authorization": f"Bearer {token}",
        "x-api-key": api_key,
        "content-type": "application/json",
    }

    last_error = None
    for idx, payload in enumerate(build_payload_candidates(prompt), start=1):
        resp = requests.post(url, headers=headers, json=payload, timeout=60)
        text = resp.text[:1200]
        print(f"[submit try {idx}] status={resp.status_code}")
        if resp.status_code == 200:
            return resp
        access_error = resp.headers.get("x-access-error")
        if access_error:
            print(f"[submit try {idx}] x-access-error={access_error}")
            if access_error == "taste_exhausted":
                raise RuntimeError(
                    "submit failed: account quota exhausted (x-access-error=taste_exhausted)"
                )
        last_error = f"status={resp.status_code}, body={text}"

    raise RuntimeError(f"submit failed: {last_error}")


def poll_until_done(token: str, poll_url: str, timeout_seconds: int = 180):
    headers = {"Authorization": f"Bearer {token}"}
    start = time.time()

    while True:
        resp = requests.get(poll_url, headers=headers, timeout=60)
        if resp.status_code != 200:
            raise RuntimeError(f"poll failed: {resp.status_code} {resp.text[:500]}")

        data = resp.json()
        status = data.get("status")
        progress = data.get("progress")
        print(f"[poll] status={status} progress={progress}")

        outputs = data.get("outputs") or []
        if outputs:
            return data

        if time.time() - start > timeout_seconds:
            raise TimeoutError("poll timeout")

        time.sleep(2.5)


def download_image(result_data: dict):
    outputs = result_data.get("outputs") or []
    if not outputs:
        raise RuntimeError("no outputs in result")

    img_url = (((outputs[0] or {}).get("image") or {}).get("presignedUrl"))
    if not img_url:
        raise RuntimeError("no presignedUrl in result")

    resp = requests.get(img_url, timeout=120)
    resp.raise_for_status()

    out = BASE_DIR / "firefly_out.png"
    out.write_bytes(resp.content)
    return out


def main():
    token = os.getenv("ADOBE_ACCESS_TOKEN", "").strip()
    api_key = os.getenv("ADOBE_API_KEY", "").strip()
    prompt = os.getenv("ADOBE_PROMPT", "A cinematic mountain sunrise with clouds").strip()

    if not token or not api_key:
        cap_token, cap_key = extract_token_and_api_key(CAPTURE_FILE)
        token = token or (cap_token or "")
        api_key = api_key or (cap_key or "")

    if not token:
        raise RuntimeError("Missing token. Set ADOBE_ACCESS_TOKEN.")
    if not api_key:
        raise RuntimeError("Missing api key. Set ADOBE_API_KEY.")

    print(f"Using api_key={api_key}")
    submit_resp = submit_job(token, api_key, prompt)

    submit_json = submit_resp.json()
    poll_url = (
        submit_resp.headers.get("x-override-status-link")
        or ((submit_json.get("links") or {}).get("result") or {}).get("href")
    )
    if not poll_url:
        raise RuntimeError(f"submit ok but no poll url: {submit_json}")

    print(f"poll_url={poll_url}")
    result_data = poll_until_done(token, poll_url)
    out = download_image(result_data)
    print(f"done -> {out}")


if __name__ == "__main__":
    main()
