# astrbot_plugin_gongten —— 高危监控禁言

QQ 群高危监控禁言插件。管理员可将指定群成员加入监控名单，被监控用户一旦发言即自动禁言并发送警告。

## ✨ 功能

- **`/高危监控 @用户`**（仅管理员）—— 将用户加入高危监控名单
- **`/高危列表`** —— 查看当前群监控名单（显示群名 + QQ 号）
- **`/脱离监控 @用户`**（仅管理员）—— 将用户移出监控名单
- **自动禁言** —— 被监控用户在群内发言时自动禁言（时长可在后台配置），同时回复警告消息
- **LLM 阻断** —— 通过 `on_llm_request` 钩子拦截被监控用户的消息，避免触发 AI 响应

## ⚙️ 配置项

在 AstrBot WebUI → 插件管理 → 本插件配置中可调整：

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `mute_duration` | int | 120 | 禁言时长（秒） |
| `mute_warning` | string | 你已在高危监控名单，无法发送信息 | 禁言警告语 |

## 📦 安装

将本插件目录放入 AstrBot 的 `data/plugins/` 下，在 WebUI 中启用即可。

```bash
cd AstrBot/data/plugins
git clone https://github.com/yourname/astrbot_plugin_gongten.git
```

## 🔧 支持的平台

- `aiocqhttp`（OneBot v11，如 NapCat / Lagrange）

## 📄 许可

[LICENSE](LICENSE)
