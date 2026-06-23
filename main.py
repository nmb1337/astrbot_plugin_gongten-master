from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api import AstrBotConfig
from astrbot.api.message_components import At


@register("astrbot_plugin_gongten", "YourName", "QQ群高危监控禁言插件", "1.0.1")
class GongTenPlugin(Star):
    """QQ 群高危监控禁言插件

    功能：
    - /监控 @用户  —— 加入高危监控名单（仅管理员）
    - /监控列表     —— 查看当前群监控名单
    - /解除监控 @用户  —— 移出高危监控名单（仅管理员）
    - 被监控用户发言时自动禁言并阻断 LLM 响应
    """

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

    # ── 数据持久化（基于 AstrBot KV 存储） ──────────────────────────

    async def _get_monitor_data(self) -> dict:
        """获取全部监控数据。

        Returns:
            dict: {group_id: {"group_name": str, "users": {user_id: {"qq": str, "nickname": str}}}}
        """
        return await self.get_kv_data("monitor_data", {})

    async def _save_monitor_data(self, data: dict):
        """保存监控数据。"""
        await self.put_kv_data("monitor_data", data)

    # ── 禁言核心逻辑 ────────────────────────────────────────────────

    async def _mute_user(self, event: AstrMessageEvent, group_id: str, user_id: str, duration: int):
        """对指定群成员执行禁言（目前仅支持 OneBot v11 / aiocqhttp）。"""
        if event.get_platform_name() != "aiocqhttp":
            logger.warning(f"当前平台 {event.get_platform_name()} 不支持禁言 API")
            return
        try:
            from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
                AiocqhttpMessageEvent,
            )
            if isinstance(event, AiocqhttpMessageEvent):
                client = event.bot
                ret = await client.api.call_action(
                    "set_group_ban",
                    group_id=int(group_id),
                    user_id=int(user_id),
                    duration=duration,
                )
                logger.info(f"禁言 {user_id} 在群 {group_id}，时长 {duration}s，结果: {ret}")
            else:
                logger.warning("event 不是 AiocqhttpMessageEvent 实例，无法获取协议端 client")
        except Exception as e:
            logger.error(f"调用禁言 API 失败: {e}")

    async def _get_group_name(self, event: AstrMessageEvent, group_id: str) -> str:
        """尝试通过 OneBot API 获取群名称，失败时返回群号。"""
        if event.get_platform_name() != "aiocqhttp":
            return group_id
        try:
            from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
                AiocqhttpMessageEvent,
            )
            if isinstance(event, AiocqhttpMessageEvent):
                info = await event.bot.api.call_action("get_group_info", group_id=int(group_id))
                if isinstance(info, dict) and info.get("group_name"):
                    return info["group_name"]
        except Exception as e:
            logger.warning(f"获取群名称失败: {e}")
        return group_id

    async def _get_user_display_name(self, event: AstrMessageEvent, group_id: str, user_id: str) -> str:
        """尝试获取群成员名片或昵称，失败时返回 QQ 号。"""
        if event.get_platform_name() != "aiocqhttp":
            return user_id
        try:
            from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
                AiocqhttpMessageEvent,
            )
            if isinstance(event, AiocqhttpMessageEvent):
                info = await event.bot.api.call_action(
                    "get_group_member_info",
                    group_id=int(group_id),
                    user_id=int(user_id),
                )
                if isinstance(info, dict):
                    return info.get("card") or info.get("nickname") or user_id
        except Exception as e:
            logger.warning(f"获取用户 {user_id} 信息失败: {e}")
        return user_id

    # ── 发送者监控检测（供指令 handler 和 LLM 钩子共用） ────────────

    async def _try_mute_monitored_sender(self, event: AstrMessageEvent) -> bool:
        """检查发送者是否处于监控名单中，若是则禁言+警告+阻断。

        调用方应在取得返回值后立即 return，不再执行后续逻辑。

        Returns:
            True: 发送者被监控并已处理（调用方应停止后续处理）
            False: 发送者未被监控，可继续正常流程
        """
        sender_id = event.get_sender_id()
        self_id = event.message_obj.self_id

        # 忽略机器人自己的消息
        if sender_id == self_id:
            return False

        group_id = event.message_obj.group_id
        if not group_id:
            return False

        data = await self._get_monitor_data()
        if group_id not in data:
            return False
        if sender_id not in data[group_id].get("users", {}):
            return False

        # ── 命中监控名单：禁言 + 警告 + 阻断 ──
        mute_duration = self.config.get("mute_duration", 120)
        mute_warning = self.config.get("mute_warning", "你已在高危监控名单，无法发送信息")

        await self._mute_user(event, group_id, sender_id, mute_duration)
        await event.send(event.plain_result(mute_warning))
        event.stop_event()
        return True

    # ── 指令：/高危监控 @用户 ─────────────────────────────────────

    @filter.command("监控")
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cmd_add_monitor(self, event: AstrMessageEvent):
        """添加群成员到高危监控名单 —— 用法: /监控 @用户"""
        # 先检查发送者自身是否被监控
        if await self._try_mute_monitored_sender(event):
            return

        target_qq = self._extract_at_qq(event)
        if not target_qq:
            yield event.plain_result("⚠️ 请 @ 要监控的用户，例如：/监控 @用户")
            return

        group_id = event.message_obj.group_id
        self_id = event.message_obj.self_id

        if target_qq == self_id:
            yield event.plain_result("⚠️ 不能监控机器人自身")
            return

        data = await self._get_monitor_data()

        if group_id not in data:
            group_name = await self._get_group_name(event, group_id)
            data[group_id] = {"group_name": group_name, "users": {}}
        else:
            data[group_id]["group_name"] = await self._get_group_name(event, group_id)

        if target_qq in data[group_id].get("users", {}):
            nickname = data[group_id]["users"][target_qq].get("nickname", target_qq)
            yield event.plain_result(f"⚠️ 用户 {nickname}({target_qq}) 已在监控名单中")
            return

        nickname = await self._get_user_display_name(event, group_id, target_qq)
        data[group_id]["users"][target_qq] = {"qq": target_qq, "nickname": nickname}
        await self._save_monitor_data(data)

        mute_sec = self.config.get("mute_duration", 120)
        yield event.plain_result(
            f"✅ 已将 {nickname}({target_qq}) 加入高危监控名单\n"
            f"📌 禁言时长: {mute_sec} 秒"
        )

    # ── 指令：/高危监控列表 ───────────────────────────────────────

    @filter.command("监控列表")
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def cmd_list_monitor(self, event: AstrMessageEvent):
        """查看当前群的高危监控名单"""
        group_id = event.message_obj.group_id
        data = await self._get_monitor_data()

        if group_id not in data or not data[group_id].get("users"):
            yield event.plain_result("📭 当前群没有高危监控用户")
            return

        group_name = data[group_id].get("group_name", group_id)
        users = data[group_id]["users"]

        lines = [f"📋 高危监控名单 —— {group_name}", "─" * 28]
        for i, (uid, uinfo) in enumerate(users.items(), 1):
            nickname = uinfo.get("nickname", uid)
            lines.append(f"  {i}. {nickname}  (QQ: {uid})")
        lines.append("─" * 28)
        lines.append(f"共 {len(users)} 人处于监控中")

        yield event.plain_result("\n".join(lines))

    # ── 指令：/高危解除 @用户 ────────────────────────────────────

    @filter.command("解除监控")
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cmd_remove_monitor(self, event: AstrMessageEvent):
        """从高危监控名单中移除群成员 —— 用法: /解除监控 @用户"""
        # 先检查发送者自身是否被监控
        if await self._try_mute_monitored_sender(event):
            return

        target_qq = self._extract_at_qq(event)
        if not target_qq:
            yield event.plain_result("⚠️ 请 @ 要解除监控的用户，例如：/解除监控 @用户")
            return

        group_id = event.message_obj.group_id
        data = await self._get_monitor_data()

        if group_id not in data or target_qq not in data[group_id].get("users", {}):
            yield event.plain_result(f"⚠️ 用户 {target_qq} 不在监控名单中")
            return

        uinfo = data[group_id]["users"].pop(target_qq)
        nickname = uinfo.get("nickname", target_qq)

        if not data[group_id]["users"]:
            del data[group_id]

        await self._save_monitor_data(data)
        yield event.plain_result(f"✅ 已将 {nickname}({target_qq}) 移出高危监控名单")

    # ── LLM 请求钩子：阻断被监控用户的 LLM 处理 ─────────────────────

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req):
        """在 LLM 被调用前检查发送者是否为被监控用户。
        若是则禁言 + 警告 + 阻止 LLM 响应。
        """
        await self._try_mute_monitored_sender(event)

    # ── 辅助方法 ───────────────────────────────────────────────────

    @staticmethod
    def _extract_at_qq(event: AstrMessageEvent) -> str | None:
        """从消息链中提取第一个 @ 目标的 QQ 号（字符串）。"""
        for comp in event.get_messages():
            if isinstance(comp, At):
                return str(comp.qq)
        return None

    async def terminate(self):
        """插件卸载/停用时的清理工作。"""
        logger.info("高危监控禁言插件已卸载")

