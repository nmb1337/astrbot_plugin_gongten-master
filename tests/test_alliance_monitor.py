from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def package_module(name: str) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__path__ = []
    return module


def load_main_module():
    astrbot = package_module("astrbot")
    api = package_module("astrbot.api")
    event = types.ModuleType("astrbot.api.event")
    star = types.ModuleType("astrbot.api.star")
    components = types.ModuleType("astrbot.api.message_components")

    class Logger:
        def debug(self, *_args, **_kwargs) -> None:
            pass

        def info(self, *_args, **_kwargs) -> None:
            pass

        def warning(self, *_args, **_kwargs) -> None:
            pass

        def error(self, *_args, **_kwargs) -> None:
            pass

    class Filter:
        class EventMessageType:
            GROUP_MESSAGE = object()

        class PermissionType:
            ADMIN = object()

        @staticmethod
        def command(*_args, **_kwargs):
            return lambda function: function

        @staticmethod
        def event_message_type(*_args, **_kwargs):
            return lambda function: function

        @staticmethod
        def permission_type(*_args, **_kwargs):
            return lambda function: function

    class Star:
        def __init__(self, context) -> None:
            self.context = context
            self.kv_data = {}

        async def get_kv_data(self, key, default):
            return self.kv_data.get(key, default)

        async def put_kv_data(self, key, data) -> None:
            self.kv_data[key] = data

    class At:
        def __init__(self, qq) -> None:
            self.qq = qq

    api.logger = Logger()
    api.AstrBotConfig = dict
    event.filter = Filter
    event.AstrMessageEvent = object
    star.Context = object
    star.Star = Star
    star.register = lambda *_args, **_kwargs: lambda plugin_class: plugin_class
    components.At = At
    sys.modules.update(
        {
            "astrbot": astrbot,
            "astrbot.api": api,
            "astrbot.api.event": event,
            "astrbot.api.star": star,
            "astrbot.api.message_components": components,
        }
    )
    spec = importlib.util.spec_from_file_location("gongten_main", PROJECT_ROOT / "main.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class AllianceMonitorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_main_module()

    def setUp(self) -> None:
        self.plugin = self.module.GongTenPlugin(object(), {"mute_duration": 120})

    def test_alliance_monitored_message_is_recalled_then_muted(self) -> None:
        calls = []

        async def recall(_event, message_id):
            calls.append(("delete_msg", message_id))
            return True

        async def mute(_event, group_id, user_id, duration):
            calls.append(("set_group_ban", group_id, user_id, duration))

        self.plugin._recall_message = recall
        self.plugin._mute_user = mute
        self.plugin.kv_data["monitor_data"] = {
            "10001": {
                "users": {
                    "20002": {
                        "qq": "20002",
                        "nickname": "目标用户",
                        "recall": True,
                    }
                }
            }
        }
        event = types.SimpleNamespace(
            message_obj=types.SimpleNamespace(
                message_id="30003", group_id="10001", self_id="40004"
            ),
            get_sender_id=lambda: "20002",
            stop_event=lambda: calls.append(("stop_event",)),
            plain_result=lambda text: text,
        )

        async def consume_listener():
            return [result async for result in self.plugin.on_group_message(event)]

        results = asyncio.run(consume_listener())

        self.assertEqual(
            [
                ("stop_event",),
                ("delete_msg", "30003"),
                ("set_group_ban", "10001", "20002", 120),
            ],
            calls,
        )
        self.assertEqual(["你已在高危监控名单，无法发送信息"], results)

    def test_alliance_monitor_command_uses_shared_monitor_list(self) -> None:
        async def group_name(_event, _group_id):
            return "测试群"

        async def display_name(_event, _group_id, _user_id):
            return "目标用户"

        self.plugin._get_group_name = group_name
        self.plugin._get_user_display_name = display_name
        event = types.SimpleNamespace(
            message_str="/联盟监控 123456789",
            message_obj=types.SimpleNamespace(group_id="10001", self_id="40004"),
            get_messages=lambda: [],
            plain_result=lambda text: text,
        )

        async def consume_command():
            return [result async for result in self.plugin.cmd_add_alliance_monitor(event)]

        results = asyncio.run(consume_command())

        user = self.plugin.kv_data["monitor_data"]["10001"]["users"]["123456789"]
        self.assertTrue(user["recall"])
        self.assertEqual("✅ 已将 目标用户(123456789) 设为联盟监控", results[0])

    def test_high_risk_command_disables_recall_for_shared_entry(self) -> None:
        async def group_name(_event, _group_id):
            return "测试群"

        async def display_name(_event, _group_id, _user_id):
            return "目标用户"

        self.plugin._get_group_name = group_name
        self.plugin._get_user_display_name = display_name
        self.plugin.kv_data["monitor_data"] = {
            "10001": {
                "group_name": "测试群",
                "users": {"123456789": {"qq": "123456789", "recall": True}},
            }
        }
        event = types.SimpleNamespace(
            message_str="/高危监控 123456789",
            message_obj=types.SimpleNamespace(group_id="10001", self_id="40004"),
            get_messages=lambda: [],
            plain_result=lambda text: text,
        )

        async def consume_command():
            return [result async for result in self.plugin.cmd_add_monitor(event)]

        results = asyncio.run(consume_command())

        user = self.plugin.kv_data["monitor_data"]["10001"]["users"]["123456789"]
        self.assertFalse(user["recall"])
        self.assertEqual("✅ 已将 目标用户(123456789) 设为高危监控", results[0])

    def test_high_risk_monitored_message_does_not_recall(self) -> None:
        calls = []

        async def recall(_event, _message_id):
            calls.append("delete_msg")
            return True

        async def mute(_event, group_id, user_id, duration):
            calls.append(("set_group_ban", group_id, user_id, duration))

        self.plugin._recall_message = recall
        self.plugin._mute_user = mute
        self.plugin.kv_data["monitor_data"] = {
            "10001": {"users": {"20002": {"qq": "20002", "recall": False}}}
        }
        event = types.SimpleNamespace(
            message_obj=types.SimpleNamespace(
                message_id="30003", group_id="10001", self_id="40004"
            ),
            get_sender_id=lambda: "20002",
            stop_event=lambda: calls.append(("stop_event",)),
            plain_result=lambda text: text,
        )

        async def consume_listener():
            return [result async for result in self.plugin.on_group_message(event)]

        results = asyncio.run(consume_listener())

        self.assertNotIn("delete_msg", calls)
        self.assertEqual([("stop_event",), ("set_group_ban", "10001", "20002", 120)], calls)
        self.assertEqual(["你已在高危监控名单，无法发送信息"], results)

    def test_legacy_alliance_entries_migrate_to_shared_monitor_list(self) -> None:
        self.plugin.kv_data["alliance_monitor_data"] = {
            "10001": {
                "group_name": "测试群",
                "users": {"20002": {"qq": "20002", "nickname": "目标用户"}},
            }
        }

        data = asyncio.run(self.plugin._get_monitor_data())

        self.assertTrue(data["10001"]["users"]["20002"]["recall"])
        self.assertEqual({}, self.plugin.kv_data["alliance_monitor_data"])

    def test_remove_monitor_removes_alliance_entry_and_unmutes(self) -> None:
        calls = []

        async def mute(_event, group_id, user_id, duration):
            calls.append((group_id, user_id, duration))

        self.plugin._mute_user = mute
        self.plugin.kv_data["monitor_data"] = {
            "10001": {
                "users": {
                    "20002": {"qq": "20002", "nickname": "目标用户", "recall": True}
                }
            }
        }
        event = types.SimpleNamespace(
            message_str="/脱离监控 20002",
            message_obj=types.SimpleNamespace(group_id="10001"),
            get_messages=lambda: [],
            plain_result=lambda text: text,
        )

        async def consume_command():
            return [result async for result in self.plugin.cmd_remove_monitor(event)]

        results = asyncio.run(consume_command())

        self.assertEqual({}, self.plugin.kv_data["monitor_data"])
        self.assertEqual([("10001", "20002", 0)], calls)
        self.assertIn("移出监控名单", results[0])

    def test_alliance_monitor_actions_use_onebot_api(self) -> None:
        calls = []

        class Api:
            async def call_action(self, action, **kwargs):
                calls.append((action, kwargs))
                return {"status": "ok"}

        client = types.SimpleNamespace(api=Api())
        event = types.SimpleNamespace()

        async def client_for_event(_event):
            return client

        self.plugin._get_client = client_for_event

        recalled = asyncio.run(self.plugin._recall_message(event, "30003"))
        asyncio.run(self.plugin._mute_user(event, "10001", "20002", 600))

        self.assertTrue(recalled)
        self.assertEqual(
            [
                ("delete_msg", {"message_id": 30003}),
                (
                    "set_group_ban",
                    {"group_id": 10001, "user_id": 20002, "duration": 600},
                ),
            ],
            calls,
        )
