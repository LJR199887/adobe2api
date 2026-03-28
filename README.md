# adobe2api

---

### ✨ 广告时间 (o゜▽゜)o☆

这是我个人独立搭建和长期维护的网站：[**Pixelle Labs**](https://www.pixellelabs.com/)

主要分享我正在开发的 **AI 创意工具**、图像/视频相关小产品和各种有趣实验。欢迎大家来逛逛、免费体验、随便玩耍 (๑•̀ㅂ•́)و✧；如果你有想法或需求，也非常欢迎反馈交流！ヾ(≧▽≦*)o

---

Adobe Firefly / OpenAI 兼容网关服务。

English README: `README_EN.md`


当前设计：

- 对外统一入口：`/v1/chat/completions`（图像 + 视频）
- 可选图像专用接口：`/v1/images/generations`
- Token 池管理（手动 Token + 自动刷新 Token）
- 管理后台 Web UI：Token / 配置 / 日志 / 刷新配置导入

## 1）部署方式

### A. 本地开发/运行

1. **安装依赖**：

```bash
pip install -r requirements.txt
```

2. **启动服务**（在 `adobe2api/` 目录下执行）：

```bash
uvicorn app:app --host 0.0.0.0 --port 6001 --reload
```

3. **访问管理后台**：

- 地址：`http://127.0.0.1:6001/`
- 默认账号密码：`admin / admin`
- 登录后可在「系统配置」修改，或编辑 `config/config.json`

### B. Docker 部署 (推荐)

本项目已提供 Docker 支持，推荐使用 Docker Compose 一键启动：

```bash
docker compose up -d --build
```

## 2）服务鉴权

服务 API Key 配置在 `config/config.json` 的 `api_key` 字段。

- 若已设置，调用时可使用以下任一方式：
  - `Authorization: Bearer <api_key>`
  - `X-API-Key: <api_key>`

管理后台和管理 API 需要先通过 `/api/v1/auth/login` 登录并持有会话 Cookie。

## 3）外部 API 使用

### 3.0 支持的模型族

当前支持如下模型族：

- `nano-banana`（图像，对应上游 `nano-banana-2`）
- `nano-banana-4k`（图像，固定 4K，对应上游 `nano-banana-2`）
- `nano-banana2`（图像，对应上游 `nano-banana-3`）
- `nano-banana2-4k`（图像，固定 4K，对应上游 `nano-banana-3`）
- `nano-banana-pro`（图像）
- `nano-banana-pro-4k`（图像，固定 4K）
- `sora2`（视频）
- `sora2-pro`（视频）
- `veo31`（视频）
- `veo31-ref`（视频，参考图模式）
- `veo31-fast`（视频）

Nano Banana 图像模型（`nano-banana-2`）：

- 命名：`model=nano-banana`，尺寸参数单独传
- 分辨率：通过 `output_resolution` 传 `1K` / `2K`
- 比例：通过 `aspect_ratio` 传 `1:1` / `16:9` / `9:16` / `4:3` / `3:4`
- 示例：
  - `model=nano-banana, output_resolution=2K, aspect_ratio=16:9`
  - `model=nano-banana, output_resolution=1K, aspect_ratio=1:1`

Nano Banana 4K 图像模型（`nano-banana-2`）：

- 命名：`model=nano-banana-4k`
- 分辨率固定为 `4K`
- 比例：通过 `aspect_ratio` 传 `1:1` / `16:9` / `9:16` / `4:3` / `3:4`
- 示例：
  - `model=nano-banana-4k, aspect_ratio=16:9`

Nano Banana 2 图像模型（`nano-banana-3`）：

- 命名：`model=nano-banana2`，尺寸参数单独传
- 分辨率：通过 `output_resolution` 传 `1K` / `2K`
- 比例：通过 `aspect_ratio` 传 `1:1` / `16:9` / `9:16` / `4:3` / `3:4`
- 示例：
  - `model=nano-banana2, output_resolution=2K, aspect_ratio=16:9`
  - `model=nano-banana2, output_resolution=1K, aspect_ratio=1:1`

Nano Banana 2 4K 图像模型（`nano-banana-3`）：

- 命名：`model=nano-banana2-4k`
- 分辨率固定为 `4K`
- 比例：通过 `aspect_ratio` 传 `1:1` / `16:9` / `9:16` / `4:3` / `3:4`
- 示例：
  - `model=nano-banana2-4k, aspect_ratio=16:9`

Nano Banana Pro 图像模型（兼容旧命名）：

- 命名：`model=nano-banana-pro`，尺寸参数单独传
- 分辨率：通过 `output_resolution` 传 `1K` / `2K`
- 比例：通过 `aspect_ratio` 传 `1:1` / `16:9` / `9:16` / `4:3` / `3:4`
- 示例：
  - `model=nano-banana-pro, output_resolution=2K, aspect_ratio=16:9`
  - `model=nano-banana-pro, output_resolution=1K, aspect_ratio=1:1`

Nano Banana Pro 4K 图像模型：

- 命名：`model=nano-banana-pro-4k`
- 分辨率固定为 `4K`
- 比例：通过 `aspect_ratio` 传 `1:1` / `16:9` / `9:16` / `4:3` / `3:4`
- 示例：
  - `model=nano-banana-pro-4k, aspect_ratio=16:9`

Sora2 视频模型：

- 命名：`model=sora2`，参数单独传
- 时长：通过 `duration` 传 `4` / `8` / `12`
- 比例：通过 `aspect_ratio` 传 `9:16` / `16:9`
- 示例：
  - `model=sora2, duration=4, aspect_ratio=16:9`
  - `model=sora2, duration=8, aspect_ratio=9:16`

Sora2 Pro 视频模型：

- 命名：`model=sora2-pro`，参数单独传
- 时长：通过 `duration` 传 `4` / `8` / `12`
- 比例：通过 `aspect_ratio` 传 `9:16` / `16:9`
- 示例：
  - `model=sora2-pro, duration=4, aspect_ratio=16:9`
  - `model=sora2-pro, duration=8, aspect_ratio=9:16`

Veo31 视频模型：

- 命名：`model=veo31`，参数单独传
- 时长：通过 `duration` 传 `4` / `6` / `8`
- 比例：通过 `aspect_ratio` 传 `16:9` / `9:16`
- 分辨率：通过 `resolution` 传 `1080p` / `720p`
- 参考模式：通过 `reference_mode` 传 `frame` 或 `image`
- 最多支持 2 张参考图：
  - 1 张：首帧参考
  - 2 张：首帧 + 尾帧参考
- 当 `reference_mode=image` 时，最多支持 3 张参考图
- 音频默认开启
- 示例：
  - `model=veo31, duration=4, aspect_ratio=16:9, resolution=1080p`
  - `model=veo31, duration=6, aspect_ratio=9:16, resolution=720p, reference_mode=image`

Veo31 Ref 视频模型（参考图模式）：

- 命名：`model=veo31-ref`，参数单独传
- 时长：通过 `duration` 传 `4` / `6` / `8`
- 比例：通过 `aspect_ratio` 传 `16:9` / `9:16`
- 分辨率：通过 `resolution` 传 `1080p` / `720p`
- 始终使用参考图模式（不是首尾帧模式）
- 最多支持 3 张参考图（映射到上游 `referenceBlobs[].usage="asset"`）
- 示例：
  - `model=veo31-ref, duration=4, aspect_ratio=9:16, resolution=720p`
  - `model=veo31-ref, duration=6, aspect_ratio=16:9, resolution=1080p`
  - `model=veo31-ref, duration=8, aspect_ratio=9:16, resolution=1080p`

Veo31 Fast 视频模型：

- 命名：`model=veo31-fast`，参数单独传
- 时长：通过 `duration` 传 `4` / `6` / `8`
- 比例：通过 `aspect_ratio` 传 `16:9` / `9:16`
- 分辨率：通过 `resolution` 传 `1080p` / `720p`
- 最多支持 2 张参考图：
  - 1 张：首帧参考
  - 2 张：首帧 + 尾帧参考
- 音频默认开启
- 示例：
  - `model=veo31-fast, duration=4, aspect_ratio=16:9, resolution=1080p`
  - `model=veo31-fast, duration=6, aspect_ratio=9:16, resolution=720p`

### 3.1 获取模型列表

```bash
curl -X GET "http://127.0.0.1:6001/v1/models" \
  -H "Authorization: Bearer <service_api_key>"
```

### 3.2 统一入口：`/v1/chat/completions`

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

图生图（在最新 user 消息中传入图片）：

```bash
curl -X POST "http://127.0.0.1:6001/v1/chat/completions" \
  -H "Authorization: Bearer <service_api_key>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "nano-banana-pro",
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

Veo31 单图语义说明：

- `veo31` / `veo31-fast` 且 `reference_mode=frame`：帧模式
  - 1 张图 => 首帧
  - 2 张图 => 首帧 + 尾帧
- `veo31-ref`，或 `veo31` 且 `reference_mode=image`：参考图模式
  - 1~3 张图 => 参考图

图生视频：

```bash
curl -X POST "http://127.0.0.1:6001/v1/chat/completions" \
  -H "Authorization: Bearer <service_api_key>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "sora2",
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

### 3.3 图像接口：`/v1/images/generations`

```bash
curl -X POST "http://127.0.0.1:6001/v1/images/generations" \
  -H "Authorization: Bearer <service_api_key>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "nano-banana-pro-4k",
    "aspect_ratio": "16:9",
    "prompt": "futuristic city skyline at dusk"
  }'
```

## 4）Cookie 导入

### 第一步：使用浏览器插件导出（推荐）

本项目提供了一个配套的浏览器插件，可以方便地从 Adobe Firefly 页面导出所需的 Cookie 数据。

- 插件源码位置：`browser-cookie-exporter/`
- 可导出最简 `cookie_*.json`（仅包含 `cookie` 字段）
- 详细说明见：`browser-cookie-exporter/README.md`

**插件安装与使用步骤：**

1. 打开 Chrome 或 Edge 浏览器的扩展管理页：`chrome://extensions`
2. 开启右上角的「开发者模式」
3. 点击「加载已解压的扩展程序」，选择项目中的 `browser-cookie-exporter/` 目录
4. 在浏览器中正常登录 [Adobe Firefly](https://firefly.adobe.com/)
5. 点击浏览器工具栏的插件图标，选择导出范围
6. 点击「导出最简 JSON」并保存文件

### 第二步：导入到项目中

拿到导出的 JSON 文件后，按照以下流程导入服务：

1. 访问并登录管理后台（默认 `http://127.0.0.1:6001/`）
2. 打开「Token 管理」页签
3. 点击「导入 Cookie」按钮
4. **方式 A：** 粘贴 JSON 文件内容到文本框；**方式 B：** 直接上传导出的 `.json` 文件
5. 点击「确认导入」（服务会自动验证 Cookie 并执行一次刷新）
6. 导入成功后，Token 列表中会显示对应的 Token，且 `自动刷新` 状态为「是」

**批量导入：** 导入弹窗支持一次上传多个文件，或粘贴包含多个账户信息的 JSON 数组。

## 5）存储路径

- 生成媒体文件：`data/generated/`
- 请求日志：`data/request_logs.jsonl`
- Token 池：`config/tokens.json`
- 服务配置：`config/config.json`
- 刷新配置（本地私有）：`config/refresh_profile.json`

生成媒体保留策略：

- `data/generated/` 下文件会保留，并通过 `/generated/*` 对外访问
- 启用按容量阈值自动清理（最旧文件优先）
  - `generated_max_size_mb`（默认 `1024`）
  - `generated_prune_size_mb`（默认 `200`）
- 当总大小超过 `generated_max_size_mb` 时，服务会删除旧文件，直到至少回收 `generated_prune_size_mb`且总大小降回阈值以内

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=leik1000/adobe2api&type=Date)](https://star-history.com/#leik1000/adobe2api&Date)
