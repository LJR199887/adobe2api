# adobe2api

---

### ✨ Ad Spot (o゜▽゜)o☆

This is my independently built and actively maintained personal website: [**Pixelle Labs**](https://www.pixellelabs.com/)

I share **AI creative tools**, image/video mini-products, and fun experiments here. You are welcome to explore, try everything out, and play around (๑•̀ㅂ•́)و✧. Feedback, ideas, and collaboration discussions are always appreciated! ヾ(≧▽≦*)o

---

Adobe Firefly/OpenAI-compatible gateway service.

Chinese README: `README.md`


Current design:

- External unified entry: `/v1/chat/completions` (image + video)
- Optional image-only endpoint: `/v1/images/generations`
- Token pool management (manual token + auto-refresh token)
- Admin web UI: token/config/logs/refresh profile import

## 1) Deployment

### A. Local Run

1. **Install dependencies**:

```bash
pip install -r requirements.txt
```

2. **Start service** (run in `adobe2api/`):

```bash
uvicorn app:app --host 0.0.0.0 --port 6001 --reload
```

3. **Access Admin UI**:

- URL: `http://127.0.0.1:6001/`
- Default login: `admin / admin`
- You can change credentials in "系统配置" (System Config) or edit `config/config.json`

### B. Docker Deployment (Recommended)

This project provides Docker support. It is recommended to use Docker Compose for one-click deployment:

```bash
docker compose up -d --build
```

## 2) Auth to this service

Service API key is configured in `config/config.json` (`api_key`).

- If set, call with either:
  - `Authorization: Bearer <api_key>`
  - `X-API-Key: <api_key>`

Admin UI and admin APIs require login session cookie via `/api/v1/auth/login`.

## 3) External API usage

### 3.0 Supported model families

Current supported model families are:

- `firefly-nano-banana` (image, maps to upstream `nano-banana-2`)
- `firefly-nano-banana2` (image, maps to upstream `nano-banana-3`)
- `firefly-nano-banana-pro` (image)
- `firefly-sora2` (video)
- `firefly-sora2-pro` (video)
- `firefly-veo31` (video)
- `firefly-veo31-ref` (video, reference-image mode)
- `firefly-veo31-fast` (video)

Nano Banana image models (`nano-banana-2`):

- Pattern: `model=firefly-nano-banana` with separate request fields
- Resolution: pass `output_resolution` as `1K` / `2K` / `4K`
- Ratio: pass `aspect_ratio` as `1:1` / `16:9` / `9:16` / `4:3` / `3:4`
- Examples:
  - `model=firefly-nano-banana, output_resolution=2K, aspect_ratio=16:9`
  - `model=firefly-nano-banana, output_resolution=4K, aspect_ratio=1:1`

Nano Banana 2 image models (`nano-banana-3`):

- Pattern: `model=firefly-nano-banana2` with separate request fields
- Resolution: pass `output_resolution` as `1K` / `2K` / `4K`
- Ratio: pass `aspect_ratio` as `1:1` / `16:9` / `9:16` / `4:3` / `3:4`
- Examples:
  - `model=firefly-nano-banana2, output_resolution=2K, aspect_ratio=16:9`
  - `model=firefly-nano-banana2, output_resolution=4K, aspect_ratio=1:1`

Nano Banana Pro image models (legacy-compatible):

- Pattern: `model=firefly-nano-banana-pro` with separate request fields
- Resolution: pass `output_resolution` as `1K` / `2K` / `4K`
- Ratio: pass `aspect_ratio` as `1:1` / `16:9` / `9:16` / `4:3` / `3:4`
- Examples:
  - `model=firefly-nano-banana-pro, output_resolution=2K, aspect_ratio=16:9`
  - `model=firefly-nano-banana-pro, output_resolution=4K, aspect_ratio=1:1`

Sora2 video models:

- Pattern: `model=firefly-sora2` with separate request fields
- Duration: pass `duration` as `4` / `8` / `12`
- Ratio: pass `aspect_ratio` as `9:16` / `16:9`
- Examples:
  - `model=firefly-sora2, duration=4, aspect_ratio=16:9`
  - `model=firefly-sora2, duration=8, aspect_ratio=9:16`

Sora2 Pro video models:

- Pattern: `model=firefly-sora2-pro` with separate request fields
- Duration: pass `duration` as `4` / `8` / `12`
- Ratio: pass `aspect_ratio` as `9:16` / `16:9`
- Examples:
  - `model=firefly-sora2-pro, duration=4, aspect_ratio=16:9`
  - `model=firefly-sora2-pro, duration=8, aspect_ratio=9:16`

Veo31 video models:

- Pattern: `model=firefly-veo31` with separate request fields
- Duration: pass `duration` as `4` / `6` / `8`
- Ratio: pass `aspect_ratio` as `16:9` / `9:16`
- Resolution: pass `resolution` as `1080p` / `720p`
- Reference mode: pass `reference_mode` as `frame` or `image`
- Supports up to 2 reference images:
  - 1 image: first-frame reference
  - 2 images: first-frame + last-frame reference
- In `reference_mode=image`, supports up to 3 reference images
- Audio defaults to enabled
- Examples:
  - `model=firefly-veo31, duration=4, aspect_ratio=16:9, resolution=1080p`
  - `model=firefly-veo31, duration=6, aspect_ratio=9:16, resolution=720p, reference_mode=image`

Veo31 Ref video models:

- Pattern: `model=firefly-veo31-ref` with separate request fields
- Duration: pass `duration` as `4` / `6` / `8`
- Ratio: pass `aspect_ratio` as `16:9` / `9:16`
- Resolution: pass `resolution` as `1080p` / `720p`
- Always uses reference image mode
- Supports up to 3 reference images
- Examples:
  - `model=firefly-veo31-ref, duration=4, aspect_ratio=9:16, resolution=720p`
  - `model=firefly-veo31-ref, duration=6, aspect_ratio=16:9, resolution=1080p`

Veo31 Fast video models:

- Pattern: `model=firefly-veo31-fast` with separate request fields
- Duration: pass `duration` as `4` / `6` / `8`
- Ratio: pass `aspect_ratio` as `16:9` / `9:16`
- Resolution: pass `resolution` as `1080p` / `720p`
- Supports up to 2 reference images:
  - 1 image: first-frame reference
  - 2 images: first-frame + last-frame reference
- Audio defaults to enabled
- Examples:
  - `model=firefly-veo31-fast, duration=4, aspect_ratio=16:9, resolution=1080p`
  - `model=firefly-veo31-fast, duration=6, aspect_ratio=9:16, resolution=720p`

### 3.1 List models

```bash
curl -X GET "http://127.0.0.1:6001/v1/models" \
  -H "Authorization: Bearer <service_api_key>"
```

### 3.2 Unified endpoint: `/v1/chat/completions`

Text-to-image:

```bash
curl -X POST "http://127.0.0.1:6001/v1/chat/completions" \
  -H "Authorization: Bearer <service_api_key>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "firefly-nano-banana-pro",
    "output_resolution": "2K",
    "aspect_ratio": "16:9",
    "messages": [{"role":"user","content":"a cinematic mountain sunrise"}]
  }'
```

Image-to-image (pass image in latest user message):

```bash
curl -X POST "http://127.0.0.1:6001/v1/chat/completions" \
  -H "Authorization: Bearer <service_api_key>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "firefly-nano-banana-pro",
    "output_resolution": "2K",
    "aspect_ratio": "16:9",
    "messages": [{
      "role":"user",
      "content":[
        {"type":"text","text":"turn this photo into watercolor style"},
        {"type":"image_url","image_url":{"url":"https://example.com/input.jpg"}}
      ]
    }]
  }'
```

Text-to-video:

```bash
curl -X POST "http://127.0.0.1:6001/v1/chat/completions" \
  -H "Authorization: Bearer <service_api_key>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "firefly-sora2",
    "duration": 4,
    "aspect_ratio": "16:9",
    "messages": [{"role":"user","content":"a drone shot over snowy forest"}]
  }'
```

Optional Sora-only controls:

- `locale`: overrides the default `en-US`
- `timeline_events`: adds structured timeline hints into the Sora prompt JSON
- `audio`: adds optional structured audio hints into the Sora prompt JSON

Example:

```bash
curl -X POST "http://127.0.0.1:6001/v1/chat/completions" \
  -H "Authorization: Bearer <service_api_key>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "firefly-sora2",
    "duration": 4,
    "aspect_ratio": "16:9",
    "locale": "ja-JP",
    "audio": {
      "sfx": "Wind howling softly",
      "voice_timbre": "Natural, calm voice"
    },
    "timeline_events": {
      "0s-2s": "Camera holds on the snowy forest",
      "2s-4s": "Drone glides forward slowly"
    },
    "messages": [{"role":"user","content":"a drone shot over snowy forest"}]
  }'
```

Veo31 single-image semantics:

- `firefly-veo31` / `firefly-veo31-fast` with `reference_mode=frame`: frame mode
  - 1 image => first frame
  - 2 images => first frame + last frame
- `firefly-veo31-ref` or `firefly-veo31` with `reference_mode=image`: reference-image mode
  - 1~3 images => reference images

Image-to-video:

```bash
curl -X POST "http://127.0.0.1:6001/v1/chat/completions" \
  -H "Authorization: Bearer <service_api_key>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "firefly-sora2",
    "duration": 8,
    "aspect_ratio": "9:16",
    "messages": [{
      "role":"user",
      "content":[
        {"type":"text","text":"animate this character walking forward"},
        {"type":"image_url","image_url":{"url":"https://example.com/character.png"}}
      ]
    }]
  }'
```

### 3.3 Image endpoint: `/v1/images/generations`

```bash
curl -X POST "http://127.0.0.1:6001/v1/images/generations" \
  -H "Authorization: Bearer <service_api_key>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "firefly-nano-banana-pro",
    "output_resolution": "4K",
    "aspect_ratio": "16:9",
    "prompt": "futuristic city skyline at dusk"
  }'
```

### 3.4 `request_id` Progress Polling

Regular external API calls now support polling task status and progress by `request_id`.

- Works with: `/v1/chat/completions` and `/v1/images/generations`
- Recommended usage: generate a client-side `request_id` and send it with the request
- Polling endpoint: `GET /v1/requests/{request_id}`
- Authentication: same as generation endpoints, using `Authorization: Bearer <service_api_key>` or `X-API-Key`
- The service also echoes `X-Request-Id` in the response headers and includes `request_id` in the JSON response body

Notes:

- If you want to start polling before the generation request returns, you must provide your own `request_id`
- If you omit it, the service will generate one for you, but you can only read it after the response finishes

Example: submit a generation request

```bash
curl -X POST "http://127.0.0.1:6001/v1/images/generations" \
  -H "Authorization: Bearer <service_api_key>" \
  -H "Content-Type: application/json" \
  -d '{
    "request_id": "demo-req-001",
    "model": "firefly-nano-banana-pro",
    "output_resolution": "2K",
    "aspect_ratio": "16:9",
    "prompt": "a cinematic mountain sunrise"
  }'
```

Example: poll progress

```bash
curl -X GET "http://127.0.0.1:6001/v1/requests/demo-req-001" \
  -H "Authorization: Bearer <service_api_key>"
```

Response example:

```json
{
  "request_id": "demo-req-001",
  "task_status": "IN_PROGRESS",
  "task_progress": 42.0,
  "upstream_job_id": "upstream-job-id",
  "retry_after": null,
  "preview_url": null,
  "preview_kind": null,
  "error": null,
  "error_code": null,
  "operation": "images.generations",
  "model": "firefly-nano-banana-pro",
  "prompt_preview": "a cinematic mountain sunrise",
  "status_code": 102,
  "source": "live",
  "done": false
}
```

- `task_status` can be `IN_PROGRESS`, `COMPLETED`, or `FAILED`
- `task_progress` ranges from `0` to `100`
- `source=live` means the payload comes from in-memory live state; `source=log` means the task already finished and the data comes from final logs
- Once the task completes, `preview_url` will be populated when a preview is available

## 4) Cookie Import

### Step 1: Export using the Browser Extension (Recommended)

This project includes a companion browser extension to help you easily export required cookies from the Adobe Firefly page.

- Extension source: `browser-cookie-exporter/`
- Exports a minimal `cookie_*.json` (containing only the `cookie` field)
- Detailed instructions: `browser-cookie-exporter/README.md`

**Installation & Usage:**

1. Open Chrome or Edge extension management: `chrome://extensions`
2. Enable "Developer mode" in the top right
3. Click "Load unpacked" and select the `browser-cookie-exporter/` directory from this project
4. Log in to [Adobe Firefly](https://firefly.adobe.com/) as usual
5. Click the extension icon in your browser toolbar and select the export scope
6. Click "Export Minimal JSON" and save the file

### Step 2: Import into the Project

Once you have the exported JSON file, follow these steps to import it:

1. Access and log in to the admin UI (default `http://127.0.0.1:6001/`)
2. Navigate to the "Token 管理" (Token Management) tab
3. Click the "导入 Cookie" (Import Cookie) button
4. **Option A:** Paste the JSON content into the text box; **Option B:** Upload the exported `.json` file directly
5. Click "Confirm Import" (the service will verify the cookies and run an initial refresh)
6. Upon success, the token will appear in the list with `自动刷新` (Auto Refresh) set to "Yes"

**Batch Import:** The import dialog supports uploading multiple files at once or pasting a JSON array containing multiple account credentials.

## 5) Storage Paths

- Generated media: `data/generated/`
- Request logs: `data/request_logs.jsonl`
- Token pool: `config/tokens.json`
- Service config: `config/config.json`
- Refresh profile (local private): `config/refresh_profile.json`

Generated media retention policy:

- Files under `data/generated/` are preserved and served via `/generated/*`
- Auto-prune is enabled by size threshold (oldest files first)
  - `generated_max_size_mb` (default `1024`)
  - `generated_prune_size_mb` (default `200`)
- When total generated file size exceeds `generated_max_size_mb`, service deletes old files until at least `generated_prune_size_mb` is reclaimed and total size falls back under threshold

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=leik1000/adobe2api&type=Date)](https://star-history.com/#leik1000/adobe2api&Date)
