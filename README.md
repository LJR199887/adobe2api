# adobe2api

Adobe Firefly / OpenAI 兼容网关服务。  
English README: `README_EN.md`

当前设计：
- 对外统一入口：`/v1/chat/completions`（图像 + 视频）
- 图像专用入口：`/v1/images/generations`
- 图像异步入口：`/api/v1/generate`
- 支持多账号 Token 池、自动刷新、管理后台、请求日志与任务进度查询

## 1. 部署方式

### A. 本地运行

```bash
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 6001 --reload
```

管理后台：
- 地址：`http://127.0.0.1:6001/`
- 默认账号密码：`admin / admin`

### B. Docker

```bash
docker compose up -d --build
```

## 2. 服务鉴权

服务 API Key 配置在 `config/config.json` 的 `api_key` 字段。

调用时可使用：
- `Authorization: Bearer <api_key>`
- `X-API-Key: <api_key>`

管理后台和管理 API 需要先通过 `/api/v1/auth/login` 登录并持有会话 Cookie。

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

## 3. 外部 API 使用

### 3.0 支持的模型

当前公开模型如下：

- `nano-banana`（图像，对应上游 `nano-banana-2`）
- `nano-banana2`（图像，对应上游 `nano-banana-3`）
- `nano-banana-pro`（图像）
- `gpt-image2`（图像，对应上游 `gpt-image` / `modelVersion=2`）
- `sora2`（视频）
- `sora2-pro`（视频）
- `veo31`（视频）
- `veo31-ref`（视频，参考图模式）
- `veo31-fast`（视频）
- `kling-v3`（视频，Kling 3.0 文生/图生视频）
- `kling-o3`（视频，Kling 3.0 Omni 文生视频）

说明：
- `nano-banana`、`nano-banana2`、`nano-banana-pro` 现在都统一通过 `output_resolution` 选择 `1K` / `2K` / `4K`
- `gpt-image2` 固定使用 `1K` 输出，可通过 `aspect_ratio` 选择比例
- 旧的 `nano-banana-4k`、`nano-banana2-4k`、`nano-banana-pro-4k` 仍保留兼容，但不会继续在 `/v1/models` 中单独展示
- 视频模型继续通过请求参数单独传 `duration`、`aspect_ratio`、`resolution`、`reference_mode`

### 3.1 Banana 图像模型

Nano Banana（`nano-banana-2`）：
- 命名：`model=nano-banana`
- 分辨率：`output_resolution=1K / 2K / 4K`
- 比例：`aspect_ratio=1:1 / 16:9 / 9:16 / 4:3 / 3:4`
- 示例：
  - `model=nano-banana, output_resolution=2K, aspect_ratio=16:9`
  - `model=nano-banana, output_resolution=1K, aspect_ratio=1:1`
  - `model=nano-banana, output_resolution=4K, aspect_ratio=16:9`

Nano Banana 2（`nano-banana-3`）：
- 命名：`model=nano-banana2`
- 分辨率：`output_resolution=1K / 2K / 4K`
- 比例：`aspect_ratio=1:1 / 16:9 / 9:16 / 4:3 / 3:4`
- 示例：
  - `model=nano-banana2, output_resolution=2K, aspect_ratio=16:9`
  - `model=nano-banana2, output_resolution=1K, aspect_ratio=1:1`
  - `model=nano-banana2, output_resolution=4K, aspect_ratio=16:9`

Nano Banana Pro：
- 命名：`model=nano-banana-pro`
- 分辨率：`output_resolution=1K / 2K / 4K`
- 比例：`aspect_ratio=1:1 / 16:9 / 9:16 / 4:3 / 3:4`
- 示例：
  - `model=nano-banana-pro, output_resolution=2K, aspect_ratio=16:9`
  - `model=nano-banana-pro, output_resolution=1K, aspect_ratio=1:1`
  - `model=nano-banana-pro, output_resolution=4K, aspect_ratio=16:9`

GPT Image2：
- 命名：`model=gpt-image2`
- 分辨率：固定 `output_resolution=1K`
- 比例：`aspect_ratio=1:1 / 16:9 / 9:16 / 4:3 / 3:4 / 3:2 / 2:3`
- 常用竖版攻略图：`model=gpt-image2, output_resolution=1K, aspect_ratio=2:3`
- 文生图、图生图、多图参考图都走同一套 `aspect_ratio -> size` 映射
- 图生图 / 多图参考图上游请求形态为：顶层 `size` + `referenceBlobs[*].usage=subject` + 空 `modelSpecificPayload`
- 多图参考图最多支持 6 张输入图
- 支持同步接口 `/v1/images/generations`、`/v1/chat/completions`，也支持异步接口 `/api/v1/generate`

### 3.2 图像尺寸映射规则

图像模型最终不会直接使用你传入的像素宽高，而是根据 `output_resolution + aspect_ratio` 自动换算成固定尺寸。
如果没有传 `aspect_ratio`，但传了 `size`，服务会先根据 `size` 自动反推比例，再套用下表。

对 `gpt-image2` 来说：
- 文生图、图生图、多图参考图都会使用下表换算出的顶层 `size`
- 图生图不会再使用 `modelSpecificPayload.size=auto`

`1K`
- `1:1` -> `1024 x 1024`
- `16:9` -> `1360 x 768`
- `9:16` -> `768 x 1360`
- `4:3` -> `1152 x 864`
- `3:4` -> `864 x 1152`
- `3:2` -> `1536 x 1024`
- `2:3` -> `1024 x 1536`

`2K`
- `1:1` -> `2048 x 2048`
- `16:9` -> `2752 x 1536`
- `9:16` -> `1536 x 2752`
- `4:3` -> `2048 x 1536`
- `3:4` -> `1536 x 2048`

`4K`
- `1:1` -> `4096 x 4096`
- `16:9` -> `5504 x 3072`
- `9:16` -> `3072 x 5504`
- `4:3` -> `4096 x 3072`
- `3:4` -> `3072 x 4096`

### 3.3 视频模型

Sora2：
- 命名：`model=sora2`
- 时长：`duration=4 / 8 / 12`
- 比例：`aspect_ratio=16:9 / 9:16`

Sora2 Pro：
- 命名：`model=sora2-pro`
- 时长：`duration=4 / 8 / 12`
- 比例：`aspect_ratio=16:9 / 9:16`

Veo31：
- 命名：`model=veo31`
- 时长：`duration=4 / 6 / 8`
- 比例：`aspect_ratio=16:9 / 9:16`
- 分辨率：`resolution=720p / 1080p`
- 参考模式：`reference_mode=frame / image`

Veo31 Ref：
- 命名：`model=veo31-ref`
- 时长：`duration=4 / 6 / 8`
- 比例：`aspect_ratio=16:9 / 9:16`
- 分辨率：`resolution=720p / 1080p`
- 固定参考图模式：`reference_mode=image`

Veo31 Fast：
- 命名：`model=veo31-fast`
- 时长：`duration=4 / 6 / 8`
- 比例：`aspect_ratio=16:9 / 9:16`
- 分辨率：`resolution=720p / 1080p`

Kling 3.0：
- 命名：`model=kling-v3`
- 时长：`duration=3~15`
- 比例：`aspect_ratio=16:9 / 9:16`
- 分辨率：不需要传
- 文生视频按上游 `kling_v3_standard_t2v` 发送；图生视频传入 1~2 张参考图时按上游 `kling_v3_standard_i2v` 发送，参考图使用 `referenceBlobs[*].usage=frame` + `order=1/2`；默认开启 `generateAudio`
- 图生视频参考图语义：1 张图 = 首帧；2 张图 = 首帧 + 尾帧

Kling 3.0 Omni：
- 命名：`model=kling-o3`
- 时长：`duration=15`
- 比例：`aspect_ratio=9:16`
- 分辨率：`resolution=720p / 1080p`
- `1080p` 按上游 `kling_o3_pro_t2v` 发送；`720p` 按上游 `kling_o3_standard_t2v` 发送；默认开启 `generateAudio`，不支持参考图

Veo31 单图/多图语义：
- `veo31` / `veo31-fast` 且 `reference_mode=frame`：帧模式
- 1 张图：首帧
- 2 张图：首帧 + 尾帧
- `veo31-ref`，或 `veo31` 且 `reference_mode=image`：参考图模式
- 1~3 张图：参考图

### 3.4 获取模型列表

```bash
curl -X GET "http://127.0.0.1:6001/v1/models" \
  -H "Authorization: Bearer <service_api_key>"
```

### 3.5 统一入口：`/v1/chat/completions`

文生图：

```bash
curl -X POST "http://127.0.0.1:6001/v1/chat/completions" \
  -H "Authorization: Bearer <service_api_key>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "nano-banana-pro",
    "output_resolution": "2K",
    "aspect_ratio": "16:9",
    "messages": [{"role":"user","content":"a cinematic mountain sunrise"}]
  }'
```

GPT Image2 文生图：

```bash
curl -X POST "http://127.0.0.1:6001/v1/chat/completions" \
  -H "Authorization: Bearer <service_api_key>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-image2",
    "output_resolution": "1K",
    "aspect_ratio": "2:3",
    "messages": [{"role":"user","content":"生成一张广州旅游攻略图"}]
  }'
```

GPT Image2 图生图（单图参考）：

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
        {"type":"text","text":"科幻风"},
        {"type":"image_url","image_url":{"url":"https://example.com/input.png"}}
      ]
    }]
  }'
```

GPT Image2 多图参考图：

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
        {"type":"text","text":"6张图片合在一起"},
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

其他图生图：

```bash
curl -X POST "http://127.0.0.1:6001/v1/chat/completions" \
  -H "Authorization: Bearer <service_api_key>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "nano-banana-pro",
    "output_resolution": "4K",
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

文生视频：

```bash
curl -X POST "http://127.0.0.1:6001/v1/chat/completions" \
  -H "Authorization: Bearer <service_api_key>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "sora2",
    "duration": 4,
    "aspect_ratio": "16:9",
    "messages": [{"role":"user","content":"a drone shot over snowy forest"}]
  }'
```

图生视频：

```bash
curl -X POST "http://127.0.0.1:6001/v1/chat/completions" \
  -H "Authorization: Bearer <service_api_key>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "veo31",
    "duration": 6,
    "aspect_ratio": "9:16",
    "resolution": "720p",
    "reference_mode": "image",
    "messages": [{
      "role":"user",
      "content":[
        {"type":"text","text":"animate this character walking forward"},
        {"type":"image_url","image_url":{"url":"https://example.com/character.png"}}
      ]
    }]
  }'
```

Kling 3.0 文生视频（异步任务，无图片时自动走 `kling_v3_standard_t2v`）：

```bash
curl -X POST "http://127.0.0.1:6001/v1/video/generations" \
  -H "Authorization: Bearer <service_api_key>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "kling-v3",
    "prompt": "奥特曼在城市废墟中打怪兽，电影级特摄镜头，环境音真实",
    "duration": 8,
    "aspect_ratio": "16:9",
    "generate_audio": true,
    "async": true
  }'
```

Kling 3.0 图生视频（异步任务，传图片时自动走 `kling_v3_standard_i2v`）：

```bash
curl -X POST "http://127.0.0.1:6001/v1/video/generations" \
  -H "Authorization: Bearer <service_api_key>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "kling-v3",
    "prompt": "让画面中的角色向镜头走来，电影级运镜，环境音真实",
    "duration": 15,
    "aspect_ratio": "9:16",
    "generate_audio": true,
    "async": true,
    "image_url": "https://example.com/character.png"
  }'
```

Kling 3.0 首尾帧图生视频（异步任务，2 张图分别作为首帧和尾帧）：

```bash
curl -X POST "http://127.0.0.1:6001/v1/video/generations" \
  -H "Authorization: Bearer <service_api_key>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "kling-v3",
    "prompt": "让角色从第一张图自然运动到第二张图，镜头平滑推进，电影级运镜",
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

### 3.6 图像接口：`/v1/images/generations`

GPT Image2 文生图：

```bash
curl -X POST "http://127.0.0.1:6001/v1/images/generations" \
  -H "Authorization: Bearer <service_api_key>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-image2",
    "output_resolution": "1K",
    "aspect_ratio": "2:3",
    "prompt": "生成一张广州旅游攻略图"
  }'
```

### 3.7 异步图像接口：`/api/v1/generate`

GPT Image2 文生图提交任务：

```bash
curl -X POST "http://127.0.0.1:6001/api/v1/generate" \
  -H "Authorization: Bearer <service_api_key>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-image2",
    "output_resolution": "1K",
    "aspect_ratio": "2:3",
    "prompt": "生成一张广州旅游攻略图"
  }'
```

GPT Image2 图生图提交任务：

```bash
curl -X POST "http://127.0.0.1:6001/api/v1/generate" \
  -H "Authorization: Bearer <service_api_key>" \
  -H "Content-Type: application/json; charset=utf-8" \
  -d '{
    "model": "gpt-image2",
    "output_resolution": "1K",
    "aspect_ratio": "16:9",
    "prompt": "科幻风",
    "messages": [{
      "role":"user",
      "content":[
        {"type":"text","text":"科幻风"},
        {"type":"image_url","image_url":{"url":"https://example.com/input.png"}}
      ]
    }]
  }'
```

GPT Image2 多图参考图提交任务：

```bash
curl -X POST "http://127.0.0.1:6001/api/v1/generate" \
  -H "Authorization: Bearer <service_api_key>" \
  -H "Content-Type: application/json; charset=utf-8" \
  -d '{
    "model": "gpt-image2",
    "output_resolution": "1K",
    "aspect_ratio": "2:3",
    "prompt": "6张图片合在一起",
    "messages": [{
      "role":"user",
      "content":[
        {"type":"text","text":"6张图片合在一起"},
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

返回示例：

```json
{
  "task_id": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "status": "pending"
}
```

查询任务：

```bash
curl -X GET "http://127.0.0.1:6001/api/v1/generate/<task_id>" \
  -H "Authorization: Bearer <service_api_key>"
```

任务完成后返回内容中会包含 `status=succeeded`、`progress=100` 和 `image_url`。

### 3.8 上游请求对齐更新（2026-04-22）

为对齐 Adobe Firefly 当前可用的上游请求形状，并减少
`422 Invalid Usage for Image Generation`，图像提交逻辑已更新：

- `nano-banana` / `nano-banana2` / `nano-banana-pro` 不再发送 `skipCai`。
- Banana 系列默认 `generationMetadata` 现在包含：
  - `module: text2image`
  - `submodule: ff-image-generate`
- Banana 系列默认 `modelSpecificPayload` 调整为：
  - `parameters.addWatermark: false`
  - 包含 `aspectRatio` 以确保按请求比例生成
- 当模型配置传入 `model_specific_payload.parameters` 时，会与默认参数合并。
- `gpt-image2` 图生图（有参考图）也会发送 `size`，按请求 `aspect_ratio + output_resolution` 计算，避免回落到原图比例。
- `gpt-image2` 默认 `generationSettings.detailLevel` 调整为 `3`，与当前上游请求形态对齐。
- 上游提交请求头 `sec-fetch-site` 改为 `cross-site`（与浏览器请求对齐）。
- 异步接口行为更新（2026-04-27）：
  - `/api/v1/generate` 现在会按请求中的 `output_resolution` 与 `aspect_ratio` 生效（包含 Banana 系列），不再回落为模型默认分辨率。

以上为内部上游对齐改动；对外 API 入参不变（`model`、`prompt`、`output_resolution`、`aspect_ratio` 等保持不变）。

## 4. Cookie 导入

项目自带浏览器插件目录：`browser-cookie-exporter/`

推荐流程：
1. 在 Chrome / Edge 打开 `chrome://extensions`
2. 开启开发者模式
3. 加载 `browser-cookie-exporter/`
4. 登录 [Adobe Firefly](https://firefly.adobe.com/)
5. 用插件导出 Cookie JSON
6. 在后台 `Token 管理` 页面导入

支持：
- 粘贴 JSON 内容
- 直接上传 `.json` 文件
- 批量导入多个账号

## 5. 存储路径

- 生成媒体文件：`data/generated/`
- 请求日志：`data/request_logs.jsonl`
- Token 池与刷新配置：`config/app.db`
- 服务配置：`config/config.json`
- 首次启动会从旧版 `config/tokens.json`、`config/refresh_profile.json` 自动迁移到 SQLite

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=leik1000/adobe2api&type=Date)](https://star-history.com/#leik1000/adobe2api&Date)
