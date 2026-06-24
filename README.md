# astrbot_plugin_gongten —— 高危监控禁言

QQ 群高危监控禁言插件。管理员可将指定群成员加入监控名单，被监控用户一旦发言即自动禁言并发送警告。

## ✨ 功能

| 指令 | 权限 | 说明 |
|---|---|---|
| `/高危监控 @用户` 或 `/高危监控 QQ号` | 仅管理员 | 将用户加入高危监控名单 |
| `/高危列表` | 所有人 | 查看当前群监控名单（群名 + QQ号 + 昵称） |
| `/脱离监控 @用户` 或 `/脱离监控 QQ号` | 仅管理员 | 移出监控名单（支持QQ号，已退群也能解） |
| `/fin联盟 @用户` 或 `/fin联盟 QQ号` | 仅管理员 | 踢出群聊并加入黑名单（延迟3.5秒执行） |
| **自动禁言** | — | 被监控用户发言 → 秒禁言 + 警告 + 阻断AI响应 |

## 🔧 更新记录 (v1.2.1)

- **脱离监控自动解禁**：`/脱离监控` 时自动调用 `set_group_ban duration=0` 解除禁言
- **双重防重复警告**：消息 ID 去重 + 同用户 8 秒内不重复禁言，杜绝刷屏
- **踢人指令优化**：`/fin联盟` 改用 `event.send()` 立即发送提示 → 延迟 4 秒 → 执行踢出，确保目标看到消息后再被踢
- **退群用户可解除**：支持 `/脱离监控 QQ号` 直接输入数字解除
- **踢人自动加黑名单**：`set_group_kick` 带 `reject_add_request=True`

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
