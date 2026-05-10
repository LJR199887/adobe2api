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
- Async image endpoint: `/api/v1/generate`
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

自动化导入 Cookie 到 Token 池：

- 在 `config/config.json` 或后台「系统配置」中设置 `automation_import_key`
- 自动化程序只需要站点地址和该密钥即可调用导入接口

```bash
curl -X POST "http://127.0.0.1:6001/api/v1/automation/import-cookie" \
  -H "Authorization: Bearer <automation_import_key>" \
  -H "Content-Type: application/json" \
  -d '{"name":"account-a","cookie":"k1=v1; k2=v2"}'
```

也可以使用请求头 `X-Token-Pool-Key: <automation_import_key>`。

批量导入：

```bash
curl -X POST "http://127.0.0.1:6001/api/v1/automation/import-cookie-batch" \
  -H "Authorization: Bearer <automation_import_key>" \
  -H "Content-Type: application/json" \
  -d '{"items":[{"name":"account-a","cookie":"k1=v1; k2=v2"},{"name":"account-b","cookie":"k3=v3; k4=v4"}]}'
```

批量接口会返回 `background_refresh.job_id`，可用下面的接口查询任务进度：

```bash
curl -X GET "http://127.0.0.1:6001/api/v1/automation/import-cookie-jobs/<job_id>" \
  -H "Authorization: Bearer <automation_import_key>"
```

## 3) External API usage

### 3.0 Supported model families

Current supported model families are:

- `firefly-nano-banana` (image, maps to upstream `nano-banana-2`)
- `firefly-nano-banana2` (image, maps to upstream `nano-banana-3`)
- `firefly-nano-banana-pro` (image)
- `gpt-image2` (image, maps to upstream `gpt-image` / `modelVersion=2`)
- `firefly-sora2` (video)
- `firefly-sora2-pro` (video)
- `firefly-veo31` (video)
- `firefly-veo31-ref` (video, reference-image mode)
- `firefly-veo31-fast` (video)
- `kling-v3` (video, Kling 3.0 text/image-to-video)
- `kling-o3` (video, Kling 3.0 Omni text-to-video)

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

GPT Image2 image model:

- Pattern: `model=gpt-image2` with separate request fields
- Resolution: fixed `output_resolution=1K`
- Ratio: pass `aspect_ratio` as `1:1` / `16:9` / `9:16` / `4:3` / `3:4` / `3:2` / `2:3`
- Common portrait poster: `model=gpt-image2, output_resolution=1K, aspect_ratio=2:3`
- Text-to-image, image-to-image, and multi-image reference all use the same `aspect_ratio -> size` mapping
- For image editing, upstream payload shape is top-level `size` + `referenceBlobs[*].usage=subject` + empty `modelSpecificPayload`
- Multi-image reference supports up to 6 input images
- Supports synchronous `/v1/images/generations` and `/v1/chat/completions`, plus async `/api/v1/generate`

### 3.0.1 Image size mapping

Image models do not use arbitrary pixel sizes directly. The service maps `output_resolution + aspect_ratio` to a fixed `size`.

For `gpt-image2`:
- text-to-image, image-to-image, and multi-image reference all use the mapped top-level `size`
- image editing no longer uses `modelSpecificPayload.size=auto`

`1K`
- `1:1` -> `1024 x 1024`
- `16:9` -> `1360 x 768`
- `9:16` -> `768 x 1360`
- `4:3` -> `1152 x 864`
- `3:4` -> `864 x 1152`
- `3:2` -> `1536 x 1024`
- `2:3` -> `1024 x 1536`

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

Kling 3.0 video model:

- Pattern: `model=kling-v3` with separate request fields
- Duration: pass `duration` as `3` through `15`
- Ratio: pass `aspect_ratio` as `16:9` / `9:16`
- Resolution: not required
- Text-to-video uses upstream `kling_v3_standard_t2v`; image-to-video with 1-2 input images uses upstream `kling_v3_standard_i2v` and sends `referenceBlobs[*].usage=frame` + `order=1/2`; enables `generateAudio` by default
- Image-to-video semantics: 1 image = first frame; 2 images = first frame + last frame

Kling 3.0 Omni video model:

- Pattern: `model=kling-o3` with separate request fields
- Duration: pass `duration` as `15`
- Ratio: pass `aspect_ratio` as `9:16`
- Resolution: pass `resolution` as `720p` / `1080p`
- Uses upstream `kling_o3_pro_t2v` for `1080p`; uses upstream `kling_o3_standard_t2v` for `720p`; enables `generateAudio` by default; does not accept reference images

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

GPT Image2 text-to-image:

```bash
curl -X POST "http://127.0.0.1:6001/v1/chat/completions" \
  -H "Authorization: Bearer <service_api_key>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-image2",
    "output_resolution": "1K",
    "aspect_ratio": "2:3",
    "messages": [{"role":"user","content":"Create a Guangzhou travel guide poster"}]
  }'
```

GPT Image2 image-to-image:

```bash
curl -X POST "http://127.0.0.1:6001/v1/chat/completions" \
  -H "Authorization: Bearer <service_api_key>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-image2",
    "output_resolution": "1K",
    "aspect_ratio": "16:9",
    "messages": [{
      "role":"user",
      "content":[
        {"type":"text","text":"sci-fi style"},
        {"type":"image_url","image_url":{"url":"https://example.com/input.png"}}
      ]
    }]
  }'
```

GPT Image2 multi-image reference:

```bash
curl -X POST "http://127.0.0.1:6001/v1/chat/completions" \
  -H "Authorization: Bearer <service_api_key>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-image2",
    "output_resolution": "1K",
    "aspect_ratio": "2:3",
    "messages": [{
      "role":"user",
      "content":[
        {"type":"text","text":"combine these 6 images into one"},
        {"type":"image_url","image_url":{"url":"https://example.com/1.png"}},
        {"type":"image_url","image_url":{"url":"https://example.com/2.png"}},
        {"type":"image_url","image_url":{"url":"https://example.com/3.png"}},
        {"type":"image_url","image_url":{"url":"https://example.com/4.png"}},
        {"type":"image_url","image_url":{"url":"https://example.com/5.png"}},
        {"type":"image_url","image_url":{"url":"https://example.com/6.png"}}
      ]
    }]
  }'
```

Other image-to-image examples:

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

Kling 3.0 text-to-video async task (no image input automatically uses `kling_v3_standard_t2v`):

```bash
curl -X POST "http://127.0.0.1:6001/v1/video/generations" \
  -H "Authorization: Bearer <service_api_key>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "kling-v3",
    "prompt": "Ultraman fights a monster in a ruined city, cinematic tokusatsu camera work, natural ambient sound",
    "duration": 8,
    "aspect_ratio": "16:9",
    "generate_audio": true,
    "async": true
  }'
```

Kling 3.0 image-to-video async task (image input automatically uses `kling_v3_standard_i2v`):

```bash
curl -X POST "http://127.0.0.1:6001/v1/video/generations" \
  -H "Authorization: Bearer <service_api_key>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "kling-v3",
    "prompt": "animate the character walking toward camera, cinematic camera motion, natural ambient sound",
    "duration": 15,
    "aspect_ratio": "9:16",
    "generate_audio": true,
    "async": true,
    "image_url": "https://example.com/character.png"
  }'
```

Kling 3.0 first/last-frame image-to-video async task:

```bash
curl -X POST "http://127.0.0.1:6001/v1/video/generations" \
  -H "Authorization: Bearer <service_api_key>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "kling-v3",
    "prompt": "animate naturally from the first image to the second image, smooth cinematic camera motion",
    "duration": 8,
    "aspect_ratio": "9:16",
    "generate_audio": true,
    "async": true,
    "image_urls": [
      "https://example.com/first-frame.png",
      "https://example.com/last-frame.png"
    ]
  }'
```

### 3.3 Image endpoint: `/v1/images/generations`

```bash
curl -X POST "http://127.0.0.1:6001/v1/images/generations" \
  -H "Authorization: Bearer <service_api_key>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-image2",
    "output_resolution": "1K",
    "aspect_ratio": "2:3",
    "prompt": "Create a Guangzhou travel guide poster"
  }'
```

### 3.4 Async image endpoint: `/api/v1/generate`

Submit GPT Image2 text-to-image task:

```bash
curl -X POST "http://127.0.0.1:6001/api/v1/generate" \
  -H "Authorization: Bearer <service_api_key>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-image2",
    "output_resolution": "1K",
    "aspect_ratio": "2:3",
    "prompt": "Create a Guangzhou travel guide poster"
  }'
```

Submit GPT Image2 image-to-image task:

```bash
curl -X POST "http://127.0.0.1:6001/api/v1/generate" \
  -H "Authorization: Bearer <service_api_key>" \
  -H "Content-Type: application/json; charset=utf-8" \
  -d '{
    "model": "gpt-image2",
    "output_resolution": "1K",
    "aspect_ratio": "16:9",
    "prompt": "sci-fi style",
    "messages": [{
      "role":"user",
      "content":[
        {"type":"text","text":"sci-fi style"},
        {"type":"image_url","image_url":{"url":"https://example.com/input.png"}}
      ]
    }]
  }'
```

Submit GPT Image2 multi-image reference task:

```bash
curl -X POST "http://127.0.0.1:6001/api/v1/generate" \
  -H "Authorization: Bearer <service_api_key>" \
  -H "Content-Type: application/json; charset=utf-8" \
  -d '{
    "model": "gpt-image2",
    "output_resolution": "1K",
    "aspect_ratio": "2:3",
    "prompt": "combine these 6 images into one",
    "messages": [{
      "role":"user",
      "content":[
        {"type":"text","text":"combine these 6 images into one"},
        {"type":"image_url","image_url":{"url":"https://example.com/1.png"}},
        {"type":"image_url","image_url":{"url":"https://example.com/2.png"}},
        {"type":"image_url","image_url":{"url":"https://example.com/3.png"}},
        {"type":"image_url","image_url":{"url":"https://example.com/4.png"}},
        {"type":"image_url","image_url":{"url":"https://example.com/5.png"}},
        {"type":"image_url","image_url":{"url":"https://example.com/6.png"}}
      ]
    }]
  }'
```

Example response:

```json
{
  "task_id": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "status": "pending"
}
```

Poll the task:

```bash
curl -X GET "http://127.0.0.1:6001/api/v1/generate/<task_id>" \
  -H "Authorization: Bearer <service_api_key>"
```

When the task completes, the response includes `status=succeeded`, `progress=100`, and `image_url`.

### 3.5 Upstream request alignment update (2026-04-22)

To match Adobe Firefly's currently accepted upstream request shape and reduce
`422 Invalid Usage for Image Generation` errors, image submit behavior was updated:

- `nano-banana` / `nano-banana2` / `nano-banana-pro` payload no longer sends `skipCai`.
- Default `generationMetadata` for banana-family models now includes:
  - `module: text2image`
  - `submodule: ff-image-generate`
- Default `modelSpecificPayload` for banana-family models is now:
  - `parameters.addWatermark: false`
  - include `aspectRatio` to enforce requested ratio
- When model-level overrides provide `model_specific_payload.parameters`, parameters are merged with defaults.
- For `gpt-image2` image-to-image requests (with references), `size` is still sent and derived from `aspect_ratio + output_resolution`, so output ratio won't silently fall back to source-image ratio.
- Default `generationSettings.detailLevel` for `gpt-image2` is now `3` to align with current upstream request shape.
- Submit headers now send `sec-fetch-site: cross-site` (browser-aligned).
- Async endpoint behavior update (2026-04-27):
  - `/api/v1/generate` now honors request `output_resolution` and `aspect_ratio` for image models (including banana-family models), instead of falling back to model default resolution.

This is an internal upstream-shape alignment only. External API fields remain unchanged (`model`, `prompt`, `output_resolution`, `aspect_ratio`, etc.).

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
- Token pool and refresh profiles: `config/app.db`
- Service config: `config/config.json`
- On first startup, legacy `config/tokens.json` and `config/refresh_profile.json` are migrated into SQLite automatically

External generated media storage:

- By default, generated images/videos are stored under `data/generated/` and returned as local service URLs.
- `use_upstream_result_url=true` returns the upstream `presignedUrl` directly, but those URLs usually expire.
- `imgbed_enabled=true` keeps the existing ImgBed upload behavior and still has the highest priority.
- `aliyun_oss_enabled=true` uploads generated assets to Alibaba Cloud OSS when ImgBed is not enabled, then returns the OSS/CDN URL.
- Common OSS fields: `aliyun_oss_endpoint`, `aliyun_oss_bucket`, `aliyun_oss_access_key_id`, `aliyun_oss_access_key_secret`, `aliyun_oss_prefix`, `aliyun_oss_public_base_url`, optional `aliyun_oss_security_token`, and optional `aliyun_oss_acl`.

Generated media retention policy:

- Files under `data/generated/` are preserved and served via `/generated/*`
- Auto-prune is enabled by size threshold (oldest files first)
  - `generated_max_size_mb` (default `1024`)
  - `generated_prune_size_mb` (default `200`)
- When total generated file size exceeds `generated_max_size_mb`, service deletes old files until at least `generated_prune_size_mb` is reclaimed and total size falls back under threshold

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=leik1000/adobe2api&type=Date)](https://star-history.com/#leik1000/adobe2api&Date)
