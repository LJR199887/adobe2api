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
- 支持同步接口 `/v1/images/generations`、`/v1/chat/completions`，也支持异步接口 `/api/v1/generate`

### 3.2 Banana 图像尺寸映射规则

这类模型最终不会直接使用你传入的像素宽高，而是根据 `output_resolution + aspect_ratio` 自动换算成固定尺寸。  
如果没有传 `aspect_ratio`，但传了 `size`，服务会先根据 `size` 自动反推比例，再套用下表。

`1K`
- `1:1` -> `1024 x 1024`
- `16:9` -> `1360 x 768`
- `9:16` -> `768 x 1360`
- `4:3` -> `1152 x 864`
- `3:4` -> `864 x 1152`

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

图生图：

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

### 3.6 图像接口：`/v1/images/generations`

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

提交任务：

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
- Token 池：`config/tokens.json`
- 服务配置：`config/config.json`
- 刷新配置：`config/refresh_profile.json`

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=leik1000/adobe2api&type=Date)](https://star-history.com/#leik1000/adobe2api&Date)
