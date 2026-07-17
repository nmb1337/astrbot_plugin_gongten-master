import asyncio
import re
import time

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api import AstrBotConfig
from astrbot.api.message_components import At


@register("astrbot_plugin_gongten", "YourName", "QQ群高危与联盟监控禁言插件", "1.3.0")
class GongTenPlugin(Star):
    """QQ 群高危监控禁言插件

    功能：
    - /高危监控 @用户  —— 加入高危监控名单（仅管理员）
    - /联盟监控 @用户 [禁言秒数] —— 加入联盟监控名单，发言即撤回并禁言（仅管理员）
    - /高危列表       —— 查看当前群监控名单
    - /脱离监控 @用户/QQ号 —— 移出高危监控名单（仅管理员，支持QQ号）
    - /fin联盟 @用户/QQ号  —— 踢出群聊并加入黑名单（仅管理员，延迟3.5秒）
    - 被监控用户发言时自动禁言并阻断 LLM 响应
    """

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        # ── 消息去重 + 近期禁言追踪（双重防重复） ──
        self._processed_msgs: dict[str, float] = {}
        self._dedup_ttl = 10
        self._recent_mutes: dict[str, float] = {}  # key:"group:user" -> timestamp

    # ═══════════════════════════════════════════════════════════════
    # 消息去重
    # ═══════════════════════════════════════════════════════════════

    def _is_duplicate(self, msg_id: str) -> bool:
        """检查消息是否已被处理过，防止框架重复派发导致多次触发。"""
        now = time.time()
        # 清理过期记录
        expired = [k for k, v in self._processed_msgs.items() if now - v > self._dedup_ttl]
        for k in expired:
            del self._processed_msgs[k]
        if msg_id in self._processed_msgs:
            logger.debug(f"去重拦截消息: {msg_id}")
            return True
        self._processed_msgs[msg_id] = now
        return False

    # ═══════════════════════════════════════════════════════════════
    # 数据持久化（基于 AstrBot KV 存储）
    # ═══════════════════════════════════════════════════════════════

    async def _get_monitor_data(self) -> dict:
        """获取全部监控数据。"""
        return await self.get_kv_data("monitor_data", {})

    async def _save_monitor_data(self, data: dict):
        """保存监控数据。"""
        await self.put_kv_data("monitor_data", data)

    async def _get_alliance_monitor_data(self) -> dict:
        """获取全部联盟监控数据。"""
        return await self.get_kv_data("alliance_monitor_data", {})

    async def _save_alliance_monitor_data(self, data: dict):
        """保存联盟监控数据。"""
        await self.put_kv_data("alliance_monitor_data", data)

    # ═══════════════════════════════════════════════════════════════
    # OneBot API 封装
    # ═══════════════════════════════════════════════════════════════

    async def _get_client(self, event: AstrMessageEvent):
        """获取 OneBot 协议端 client，非 aiocqhttp 平台返回 None。"""
        if event.get_platform_name() != "aiocqhttp":
            return None
        try:
            from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
                AiocqhttpMessageEvent,
            )
            if isinstance(event, AiocqhttpMessageEvent):
                return event.bot
        except Exception:
            pass
        return None

    async def _mute_user(self, event: AstrMessageEvent, group_id: str, user_id: str, duration: int):
        """禁言群成员。"""
        client = await self._get_client(event)
        if not client:
            logger.warning("无法获取协议端 client，禁言失败")
            return
        try:
            ret = await client.api.call_action(
                "set_group_ban",
                group_id=int(group_id),
                user_id=int(user_id),
                duration=duration,
            )
            logger.info(f"禁言 {user_id} 在群 {group_id}，时长 {duration}s，结果: {ret}")
        except Exception as e:
            logger.error(f"禁言失败: {e}")

    async def _recall_message(self, event: AstrMessageEvent, message_id: str) -> bool:
        """撤回一条群消息。"""
        client = await self._get_client(event)
        if not client:
            logger.warning("无法获取协议端 client，撤回失败")
            return False
        try:
            ret = await client.api.call_action("delete_msg", message_id=int(message_id))
            logger.info(f"撤回消息 {message_id}，结果: {ret}")
            return True
        except Exception as e:
            logger.error(f"撤回消息失败: {e}")
            return False

    async def _kick_user(self, event: AstrMessageEvent, group_id: str, user_id: str) -> bool:
        """踢出群成员并加入黑名单（reject_add_request=True）。"""
        client = await self._get_client(event)
        if not client:
            logger.warning("无法获取协议端 client，踢人失败")
            return False
        try:
            ret = await client.api.call_action(
                "set_group_kick",
                group_id=int(group_id),
                user_id=int(user_id),
                reject_add_request=True,  # 加入群黑名单
            )
            logger.info(f"踢出 {user_id} 从群 {group_id}，结果: {ret}")
            return True
        except Exception as e:
            logger.error(f"踢人失败: {e}")
            return False

    async def _get_group_name(self, event: AstrMessageEvent, group_id: str) -> str:
        """获取群名称，失败返回群号。"""
        client = await self._get_client(event)
        if not client:
            return group_id
        try:
            info = await client.api.call_action("get_group_info", group_id=int(group_id))
            if isinstance(info, dict) and info.get("group_name"):
                return info["group_name"]
        except Exception as e:
            logger.warning(f"获取群名失败: {e}")
        return group_id

    async def _get_user_display_name(self, event: AstrMessageEvent, group_id: str, user_id: str) -> str:
        """获取群成员名片/昵称，失败返回 QQ 号。"""
        client = await self._get_client(event)
        if not client:
            return user_id
        try:
            info = await client.api.call_action(
                "get_group_member_info",
                group_id=int(group_id),
                user_id=int(user_id),
            )
            if isinstance(info, dict):
                return info.get("card") or info.get("nickname") or user_id
        except Exception as e:
            logger.warning(f"获取用户 {user_id} 信息失败（可能已退群）: {e}")
        return user_id

    # ═══════════════════════════════════════════════════════════════
    # 指令：/高危监控 @用户
    # ═══════════════════════════════════════════════════════════════

    @filter.command("高危监控")
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cmd_add_monitor(self, event: AstrMessageEvent):
        """添加群成员到高危监控名单 —— 用法: /高危监控 @用户"""
        target_qq = self._extract_target_qq(event)
        if not target_qq:
            yield event.plain_result("⚠️ 请 @ 要监控的用户，或输入QQ号：/高危监控 @用户")
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

        yield event.plain_result(f"✅ 已将 {nickname}({target_qq}) 加入高危监控名单")

    # ═══════════════════════════════════════════════════════════════
    # 指令：/联盟监控 @用户 [禁言秒数] / QQ号 [禁言秒数]
    # ═══════════════════════════════════════════════════════════════

    @filter.command("联盟监控")
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cmd_add_alliance_monitor(self, event: AstrMessageEvent):
        """监控群成员：目标发言时立即撤回并按配置禁言。"""
        target_qq = self._extract_target_qq(event)
        if not target_qq:
            yield event.plain_result(
                "⚠️ 请 @ 要监控的用户，或输入 QQ 号：/联盟监控 @用户 [禁言秒数]"
            )
            return

        group_id = str(event.message_obj.group_id)
        self_id = str(event.message_obj.self_id)
        if target_qq == self_id:
            yield event.plain_result("⚠️ 不能监控机器人自身")
            return

        duration = self._extract_duration(event, target_qq)
        if duration is None:
            duration = self._get_alliance_mute_duration()
        if duration < 1 or duration > 2_592_000:
            yield event.plain_result("⚠️ 禁言秒数必须在 1 到 2592000 之间")
            return

        data = await self._get_alliance_monitor_data()
        if group_id not in data:
            group_name = await self._get_group_name(event, group_id)
            data[group_id] = {"group_name": group_name, "users": {}}
        else:
            data[group_id]["group_name"] = await self._get_group_name(event, group_id)

        nickname = await self._get_user_display_name(event, group_id, target_qq)
        data[group_id]["users"][target_qq] = {
            "qq": target_qq,
            "nickname": nickname,
            "mute_duration": duration,
        }
        await self._save_alliance_monitor_data(data)
        yield event.plain_result(
            f"✅ 已将 {nickname}({target_qq}) 加入联盟监控：发言将立即撤回并禁言 {duration} 秒"
        )

    @filter.command("联盟列表")
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def cmd_list_alliance_monitor(self, event: AstrMessageEvent):
        """查看当前群的联盟监控名单和禁言秒数。"""
        group_id = str(event.message_obj.group_id)
        data = await self._get_alliance_monitor_data()
        if group_id not in data or not data[group_id].get("users"):
            yield event.plain_result("📭 当前群没有联盟监控用户")
            return

        group_name = data[group_id].get("group_name", group_id)
        lines = [f"📋 联盟监控名单 —— {group_name}", "─" * 28]
        for index, (user_id, user_info) in enumerate(data[group_id]["users"].items(), 1):
            nickname = user_info.get("nickname", user_id)
            duration = user_info.get("mute_duration", self._get_alliance_mute_duration())
            lines.append(f"  {index}. {nickname} (QQ: {user_id}) 禁言 {duration} 秒")
        lines.append("─" * 28)
        lines.append(f"共 {len(data[group_id]['users'])} 人处于联盟监控中")
        yield event.plain_result("\n".join(lines))

    @filter.command("解除联盟监控")
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cmd_remove_alliance_monitor(self, event: AstrMessageEvent):
        """从联盟监控名单中移除群成员。"""
        target_qq = self._extract_target_qq(event)
        if not target_qq:
            yield event.plain_result(
                "⚠️ 请 @ 要解除的用户，或输入 QQ 号：/解除联盟监控 @用户"
            )
            return

        group_id = str(event.message_obj.group_id)
        data = await self._get_alliance_monitor_data()
        if group_id not in data or target_qq not in data[group_id].get("users", {}):
            yield event.plain_result(f"⚠️ 用户 {target_qq} 不在联盟监控名单中")
            return

        user_info = data[group_id]["users"].pop(target_qq)
        if not data[group_id]["users"]:
            del data[group_id]
        await self._save_alliance_monitor_data(data)
        await self._mute_user(event, group_id, target_qq, 0)
        nickname = user_info.get("nickname", target_qq)
        yield event.plain_result(f"✅ 已将 {nickname}({target_qq}) 移出联盟监控名单，并已解除禁言")

    # ═══════════════════════════════════════════════════════════════
    # 指令：/高危列表
    # ═══════════════════════════════════════════════════════════════

    @filter.command("高危列表")
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

    # ═══════════════════════════════════════════════════════════════
    # 指令：/脱离监控 @用户 / QQ号
    # ═══════════════════════════════════════════════════════════════

    @filter.command("脱离监控")
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cmd_remove_monitor(self, event: AstrMessageEvent):
        """从高危监控名单中移除群成员 —— 用法: /脱离监控 @用户 或 /脱离监控 QQ号"""
        target_qq = self._extract_target_qq(event)
        if not target_qq:
            yield event.plain_result("⚠️ 请 @ 要解除的用户，或输入QQ号：/脱离监控 @用户")
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

        # 自动解除禁言（duration=0）
        await self._mute_user(event, group_id, target_qq, 0)

        yield event.plain_result(f"✅ 已将 {nickname}({target_qq}) 移出高危监控名单，并已解除禁言")

    # ═══════════════════════════════════════════════════════════════
    # 指令：/fin联盟 @用户 / QQ号 （踢人 + 黑名单 + 延迟）
    # ═══════════════════════════════════════════════════════════════

    @filter.command("fin联盟")
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cmd_fin_kick(self, event: AstrMessageEvent):
        """踢出群成员并加入黑名单 —— 用法: /fin联盟 @用户 或 /fin联盟 QQ号"""
        target_qq = self._extract_target_qq(event)
        if not target_qq:
            yield event.plain_result("⚠️ 请 @ 要踢出的用户，或输入QQ号：/fin联盟 @用户")
            return

        group_id = event.message_obj.group_id
        self_id = event.message_obj.self_id

        if target_qq == self_id:
            yield event.plain_result("⚠️ 不能踢出机器人自身")
            return

        nickname = await self._get_user_display_name(event, group_id, target_qq)

        # 趁 event 还有效，先拿到协议端 client
        client = await self._get_client(event)
        if not client:
            yield event.plain_result("❌ 无法获取协议端，踢人失败")
            return

        # 用 asyncio.create_task 把踢人丢到后台延迟执行
        # 这样 yield 的消息能立刻发到群里，目标看得到
        asyncio.create_task(
            self._bg_kick(client, group_id, target_qq, nickname)
        )

        # 立刻发出假消息（框架收到 yield 后马上发送）
        yield event.plain_result(f"🚫 {nickname}({target_qq}) 已被拉入联盟黑名单")

    async def _bg_kick(self, client, group_id: str, target_qq: str, nickname: str):
        """后台任务：延迟 4 秒后执行踢人 + 黑名单 + 清理监控名单。"""
        try:
            await asyncio.sleep(4)

            ret = await client.api.call_action(
                "set_group_kick",
                group_id=int(group_id),
                user_id=int(target_qq),
                reject_add_request=True,
            )
            logger.info(f"后台踢出 {target_qq} 从群 {group_id}，结果: {ret}")

            # 同步清理监控名单
            data = await self._get_monitor_data()
            if group_id in data and target_qq in data[group_id].get("users", {}):
                del data[group_id]["users"][target_qq]
                if not data[group_id]["users"]:
                    del data[group_id]
                await self._save_monitor_data(data)
        except Exception as e:
            logger.error(f"后台踢人失败: {e}")

    # ═══════════════════════════════════════════════════════════════
    # 群消息监听：检测被监控用户发言
    # ═══════════════════════════════════════════════════════════════

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        """监听所有群消息，执行联盟监控或高危监控处理。"""
        msg_id = str(event.message_obj.message_id)

        # 消息去重：防止框架重复派发导致多次警告
        if self._is_duplicate(msg_id):
            return

        sender_id = str(event.get_sender_id())
        self_id = str(event.message_obj.self_id)

        # 忽略机器人自己的消息
        if not sender_id or sender_id == self_id:
            return

        group_id = str(event.message_obj.group_id)
        if not group_id:
            return

        alliance_data = await self._get_alliance_monitor_data()
        alliance_user = alliance_data.get(group_id, {}).get("users", {}).get(sender_id)
        if alliance_user:
            event.stop_event()
            duration = alliance_user.get("mute_duration", self._get_alliance_mute_duration())
            await self._recall_message(event, msg_id)
            await self._mute_user(event, group_id, sender_id, duration)
            return

        data = await self._get_monitor_data()
        if group_id not in data:
            return
        if sender_id not in data[group_id].get("users", {}):
            return

        # ── 近期禁言去重：同一用户 8 秒内不重复处理 ──
        mute_key = f"{group_id}:{sender_id}"
        now = time.time()
        # 清理过期
        expired_mutes = [k for k, v in self._recent_mutes.items() if now - v > 30]
        for k in expired_mutes:
            del self._recent_mutes[k]
        if mute_key in self._recent_mutes and now - self._recent_mutes[mute_key] < 8:
            logger.info(f"防重复禁言拦截: {mute_key}")
            return
        self._recent_mutes[mute_key] = now

        # ── 命中监控名单：先阻断 → 禁言 → 警告 ──
        event.stop_event()

        mute_duration = self.config.get("mute_duration", 120)
        mute_warning = self.config.get("mute_warning", "你已在高危监控名单，无法发送信息")

        await self._mute_user(event, group_id, sender_id, mute_duration)
        yield event.plain_result(mute_warning)

    # ═══════════════════════════════════════════════════════════════
    # 辅助方法
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def _extract_at_qq(event: AstrMessageEvent) -> str | None:
        """从消息链中提取第一个 @ 目标的 QQ 号。"""
        for comp in event.get_messages():
            if isinstance(comp, At):
                return str(comp.qq)
        return None

    @staticmethod
    def _extract_qq_from_text(event: AstrMessageEvent) -> str | None:
        """从纯文本中提取 QQ 号（5-11位数字），用于退群用户无法 @ 时手动输入QQ号。"""
        text = event.message_str
        match = re.search(r'\b(\d{5,11})\b', text)
        if match:
            return match.group(1)
        return None

    @staticmethod
    def _extract_target_qq(event: AstrMessageEvent) -> str | None:
        """提取目标 QQ 号：优先 @ 提取，回退到文本数字提取。"""
        qq = GongTenPlugin._extract_at_qq(event)
        if qq:
            return qq
        return GongTenPlugin._extract_qq_from_text(event)

    def _get_alliance_mute_duration(self) -> int:
        """Return a valid default alliance-monitor mute duration in seconds."""
        duration = self.config.get("alliance_mute_duration", 120)
        if isinstance(duration, bool) or not isinstance(duration, int):
            return 120
        return duration

    @staticmethod
    def _extract_duration(event: AstrMessageEvent, target_qq: str) -> int | None:
        """Extract the optional duration after an @ target or direct QQ target."""
        numbers = re.findall(r"\b([1-9]\d*)\b", event.message_str)
        if GongTenPlugin._extract_at_qq(event):
            return int(numbers[-1]) if numbers else None
        for index, value in enumerate(numbers):
            if value == target_qq and index + 1 < len(numbers):
                return int(numbers[index + 1])
        return None

    async def terminate(self):
        """插件卸载/停用时的清理工作。"""
        logger.info("高危监控禁言插件已卸载")


