# Adobe Cookie 导出插件

这是一个用于 Chrome 或 Edge 的小插件，可以从 Adobe / Firefly 页面导出当前浏览器 Cookie，并生成 `adobe2api` 可直接导入的最小 JSON 格式。

## 导出格式

```json
{
  "cookie": "k1=v1; k2=v2"
}
```

## 安装

1. 打开 `chrome://extensions` 或 `edge://extensions`
2. 开启开发者模式
3. 点击「加载已解压的扩展程序」
4. 选择本项目里的 `browser-cookie-exporter/` 目录

## 使用

1. 在浏览器中登录 Adobe 或 Firefly
2. 打开插件弹窗
3. 选择导出范围：
   - `Adobe domains (recommended)`：推荐，导出 Adobe 相关域名 Cookie
   - `Current site`：只导出当前站点 Cookie
4. 点击 `Export Minimal JSON` 导出 JSON

## 导入到 adobe2api

先在 `config/config.json` 或后台「系统配置」中设置 `automation_import_key`。自动化程序只需要拿到项目网站地址和这个密钥，就可以把 Cookie 导入 Token 池。

```bash
curl -X POST "http://127.0.0.1:6001/api/v1/automation/import-cookie" \
  -H "Authorization: Bearer <automation_import_key>" \
  -H "Content-Type: application/json" \
  -d '{"name":"my-account","cookie":"k1=v1; k2=v2"}'
```

也可以使用请求头 `X-Token-Pool-Key: <automation_import_key>`。

## 无痕窗口支持

插件会从当前活动标签页所在的 Cookie 存储中导出 Cookie。如果你在无痕窗口里打开 Adobe 或 Firefly，并从无痕标签页打开插件，导出的就是无痕窗口里的 Cookie。

启用方式：

1. 打开 `chrome://extensions` 或 `edge://extensions`
2. 进入本插件的详情页
3. 开启「允许在无痕模式下运行」
4. 在无痕窗口中打开 Adobe 或 Firefly 并登录
5. 从该无痕标签页打开插件并导出 JSON
