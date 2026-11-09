from codex_wechat.errors import UserFacingError, attachment_reason, backend_reason
import asyncio
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from codex_wechat.app import Bridge, parse_control
from codex_wechat.backend import CodexBackend, CodexRpcError
from codex_wechat.media import OUTPUT_HINT, MediaError
from codex_wechat.latency import Latency
from codex_wechat.command_specs import HELP, HELP_ALL
from codex_wechat.commands import dispatch, handle, info, reload_backend_definitions
from codex_wechat.configuration import (DEFAULT_ACK_TEMPLATE, DEFAULT_COMPLETION_TEMPLATE,
                           completion_template, processing_template)
from codex_wechat.prompts import (
    PING_REPLY,
    RESTART_NOT_SERVICE,
    UNSUPPORTED_MESSAGE_TYPE,
    render_ack,
    session_summary,
    setting_summary,
    SettingsView,
  settings_summary,
    status_summary,
)
from codex_wechat.storage import Store
from codex_wechat.wechat import trusted_url
from codex_wechat.profiles import backend_label, default_backends, definitions, ensure_backend_config


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.bridge = Bridge(self.store, {"user": "owner"}, {"backends": {
            "codex": {}, "deepseek": {"command": ["fake-deepseek", "app-server"]}}})

    def tearDown(self):
        self.store.lock.close()
        self.temp.cleanup()

    def message(self, identity="1", user="owner", text="hello"):
        return {"message_id": identity, "from_user_id": user, "message_type": 1,
                "context_token": "context", "item_list": [{"type": 1,
                                                           "text_item": {"text": text}}]}

    async def test_identity_group_filter_and_cursor_dedup(self):
        group = self.message("3")
        group["group_id"] = "group"
        await self.bridge.receive({"msgs": [self.message(), self.message(),
            self.message("2", "stranger"), group], "get_updates_buf": "next"})
        saved = self.store.load("bridge")
        self.assertEqual([x["id"] for x in saved["inbox"]], ["1"])
        self.assertEqual(saved["cursor"], "next")
        restarted = Bridge(self.store, {"user": "owner"}, {"backends": {
            "codex": {}, "deepseek": {"command": ["fake-deepseek", "app-server"]}}})
        await restarted.receive({"msgs": [self.message()]})
        self.assertEqual(len(restarted.state["inbox"]), 1)

    async def test_private_unsupported_message_type_is_answered_once(self):
        message = {"message_id": "unsupported", "from_user_id": "owner",
                   "message_type": 9, "context_token": "context", "item_list": []}
        await self.bridge.receive({"msgs": [message, message]})
        self.assertEqual([entry["text"] for entry in self.store.load("bridge")["outbox"]],
                         [UNSUPPORTED_MESSAGE_TYPE])

    async def test_group_and_outbound_types_stay_silent(self):
        group = {"message_id": "group-unsupported", "from_user_id": "owner",
                 "message_type": 9, "context_token": "context",
                 "group_id": "group", "item_list": []}
        outbound = {"message_id": "outbound", "from_user_id": "owner",
                    "message_type": 2, "context_token": "context", "item_list": []}
        await self.bridge.receive({"msgs": [group, outbound]})
        self.assertFalse(self.store.load("bridge")["outbox"])

    async def test_item_timestamp_supplies_missing_message_timestamp(self):
        message = self.message("latency")
        message["item_list"][0]["create_time_ms"] = self.bridge.latency.started_ms
        await self.bridge.receive({"msgs": [message]})
        self.assertIn("ingress_ms", self.bridge.state["inbox"][0])

    async def test_no_context_no_execution_and_stop_clears_queue(self):
        message = self.message()
        del message["context_token"]
        await self.bridge.receive({"msgs": [message]})
        self.assertFalse(self.bridge.state["inbox"])
        await self.bridge.receive({"msgs": [self.message("2")]})
        await self.bridge.control("/stop")
        self.assertFalse(self.store.load("bridge")["inbox"])

    async def test_command_parser_trims_surrounding_whitespace(self):
        self.assertEqual(parse_control("/restart \t"), ("/restart", ""))
        self.assertEqual(parse_control("  /switch   2  "), ("/switch", "2"))
        self.assertEqual(parse_control("/get /tmp/a b.txt"),
                         ("/get", "/tmp/a b.txt"))
        self.assertEqual(parse_control("   "), ("", ""))

    async def test_private_state_and_utf8_chunks(self):
        self.bridge.state["context"] = "context"
        self.bridge.reply("汉" * 2500)
        entries = self.store.load("bridge")["outbox"]
        self.assertEqual("".join(x["text"] for x in entries), "汉" * 2500)
        self.assertTrue(all(len(x["text"].encode()) <= 3000 for x in entries))
        if os.name != "nt":
            self.assertEqual(os.stat(Path(self.temp.name) / "bridge.json").st_mode & 0o777, 0o600)

    async def test_default_completion_notice_follows_answer(self):
        self.bridge.state["context"] = "context"
        await self.bridge.deliver_answer("answer", notify_completion=True)
        texts = [entry.get("text") for entry in self.store.load("bridge")["outbox"]]
        self.assertEqual(texts[0], "answer")
        self.assertTrue(texts[1].startswith("`OUT > "))

    async def test_output_hint_is_concise_origin_marker(self):
        self.assertEqual(
            OUTPUT_HINT,
            "\n\n[User from WeChat. Keep replies mobile-friendly.]")

    async def test_stdio_backend_final_only_and_resume(self):
        fake_server = Path(__file__).resolve().parents[1] / "support" / "fake_server.py"
        backend = CodexBackend({"command": [sys.executable, str(fake_server)]},
                               self.temp.name, lambda _: None)
        try:
            await backend.start()
            thread = await backend.thread()
            self.assertEqual(await backend.thread(thread), thread)
            answer = await asyncio.wait_for(backend.run(thread, "hello"), 3)
            self.assertEqual(answer.text, "hello")
            self.assertTrue(answer.completed)
        finally:
            await backend.close()

    async def test_credential_redirect_boundary(self):
        for value in ("http://ilinkai.weixin.qq.com", "https://evil.example",
                      "https://ilinkai.weixin.qq.com.evil.example", "https://u@ilinkai.weixin.qq.com"):
            with self.assertRaises(RuntimeError):
                trusted_url(value)
        self.assertEqual(trusted_url("https://ilinkai.weixin.qq.com/"), "https://ilinkai.weixin.qq.com")

    async def test_ping_enqueues_echo_without_starting_backend(self):
        self.bridge.state["context"] = "context"
        self.bridge.latency.sends.append(50)
        await self.bridge.control("/ping", received={"ingress_ms": 120})
        outbox = self.store.load("bridge")["outbox"]
        self.assertEqual([entry["text"] for entry in outbox],
                         ["微信 ─120/50ms", "桥接 ─unavailable(无可用目标)─ 提供方"])
        self.assertNotIn("ping", outbox[-1])
        self.assertFalse(self.bridge.backend_started)

    async def test_settings_remain_available_without_backend(self):
        self.bridge.state["context"] = "context"
        with patch("codex_wechat.commands.autostart.status", AsyncMock(return_value=True)):
            await self.bridge.control("/settings")
        self.assertIn("media.maxMiB", self.store.load("bridge")["outbox"][-1]["text"])
        self.assertFalse(self.bridge.backend_started)

    async def test_latency_shows_only_wechat_channel(self):
        stats = Latency()
        stats.sends.append(50)
        values = stats.values(120)
        self.assertEqual(values, {"ingress_ms": "120", "egress_ms": "50"})
        self.assertEqual(render_ack(DEFAULT_ACK_TEMPLATE, {
            "turn_total_tokens": "12.3K", "ingress_ms": "120"}),
            "`IN < ~12.3K (120ms)`")
        self.assertEqual(render_ack(DEFAULT_COMPLETION_TEMPLATE, {
            "visible_output_tokens": "1.2K",
            "tokens_per_second": "38",
            "final_answer_duration_s": "32",
        }), "`OUT > 1.2K, ~38 tok/s in 32s`")
        self.assertEqual(render_ack(PING_REPLY, values), "微信 ─120/50ms")
        self.assertEqual(stats.values(), {"ingress_ms": "-", "egress_ms": "50"})

    async def test_last_send_sample_survives_bridge_restart(self):
        self.bridge.state["lastSendMs"] = 123.4
        self.bridge.save()
        restarted = Bridge(self.store, {"user": "owner"}, {})
        self.assertEqual(restarted.prompt_fields()["egress_ms"], "123")

    async def test_ack_text_controls_notice_without_switch(self):
        self.bridge.state["context"] = "context"
        self.bridge.state["processingNotice"] = "{ingress_ms}|{egress_ms}ms"
        self.bridge.latency.sends.append(80)
        self.bridge.acknowledge({"ingress_ms": 120})
        self.assertEqual(self.store.load("bridge")["outbox"][-1]["text"], "120|80ms")

        await self.bridge.control('/settings ack ""')
        self.assertEqual(processing_template(self.bridge.config, self.bridge.state), "")
        self.assertEqual(self.store.load("bridge")["outbox"][-1]["text"],
                         '```\nack: \n```')
        before = len(self.bridge.state["outbox"])
        self.bridge.acknowledge({"ingress_ms": 120})
        self.assertEqual(len(self.bridge.state["outbox"]), before)

    async def test_notice_templates_are_not_silently_rewritten(self):
        self.assertEqual(processing_template({"processingNotice": "{model}"}, {}), "{model}")
        self.assertEqual(completion_template({}, {"completionNotice": "{output_tokens}"}),
                         "{output_tokens}")
        self.assertEqual(processing_template({}, {"processingNotice": ""}), "")

    async def test_user_prompt_formats(self):
        self.assertEqual(session_summary(
            "已切换会话", name="微信附件", model="example", effort="high", cwd="~/project",
            status="空闲", session_id="01a13162-8d25-7883-ae2b-c70389dc9d58"),
            "已切换会话\n```\n会话: 微信附件\nID: 01a13162\n状态: 空闲\n"
            "模型: example (High)\n目录: ~/project\n```")
        self.assertEqual(setting_summary(model="example", effort="high", cwd="~/project", field="model"),
                         "模型: example (High)")
        self.assertEqual(setting_summary(model="example", effort="high", cwd="~/project", field="effort"),
                         "强度: High")
        self.assertEqual(setting_summary(model="example", effort="high", cwd="~/project", field="cwd"),
                         "目录: ~/project")
        self.assertEqual(settings_summary(SettingsView(notice=DEFAULT_ACK_TEMPLATE,
                                          completion_notice=DEFAULT_COMPLETION_TEMPLATE,
                                          attachment_mib=20)),
                         "```\nack: IN < ~{turn_total_tokens} ({ingress_ms}ms)\n"
                         "finish: OUT > {visible_output_tokens}, "
                         "~{tokens_per_second} tok/s in {final_answer_duration_s}s\n"
                         "media.maxMiB: 20\n```\n\n"
                         "以上设置可通过 `/settings [option] [value]` 更改；"
                         "使用 `/help settings` 查看设置说明。")
        status = status_summary(status="运行中", queued=2, pending=0, failed=0,
            backend="Codex", name="微信附件", model="example", effort="high", cwd="~/project",
            connection="本地后台", provider="openai",
            sandbox={"type": "workspaceWrite"}, approval="on-request", reviewer="user",
            collaboration={"mode": "default"},
            usage={"last": {"totalTokens": 12345},
                   "total": {"totalTokens": 34567, "inputTokens": 30000, "outputTokens": 4567},
                   "modelContextWindow": 200000},
            rate_limits={"rateLimits": {"primary": {"usedPercent": 10, "windowDurationMins": 300}}})
        for expected in ("状态: 运行中", "排队消息: 2", "后端: Codex", "模型: example (High)",
                         "权限: 工作区可写", "协作: 默认", "服务: 本地后台",
                         "Token: 34.6K total\n       30K input + 4.6K output",
                         "上下文: 剩余 94% (12.3K / 200K)", "限额: 5小时剩余 90%"):
            self.assertIn(expected, status)
        for unwanted in ("审批: 0", "发送失败: 0", "Session", "·", "->", "→"):
            self.assertNotIn(unwanted, status)
        self.assertTrue(HELP.startswith("`/s` `/status` 当前状态"))
        for command in ("/status", "/backend {名称}", "`/settings` 查看设置"):
            self.assertIn(command, HELP)
        for command in ("/approve", "/deny", "/answer", "/get", "/getfile",
                        "/cwd", "/switch", "/ping", "/version"):
            self.assertNotIn(f"`{command} ", HELP)
            self.assertNotIn(f"`{command}`", HELP)
            self.assertTrue(f"`{command} " in HELP_ALL or f"`{command}`" in HELP_ALL)
        self.assertNotIn("```", HELP)
        self.assertEqual(backend_reason(RuntimeError("WebSocket 握手失败")), "后端连接中断")
        self.assertEqual(attachment_reason(MediaError("附件超过本地大小限制")), "文件超过大小限制")

    def test_reason_tables_keep_first_match_priority(self):
        # Rules are ordered data now; an earlier rule must win over a later one.
        self.assertEqual(
            attachment_reason(MediaError("附件超过大小限制，cdn 请求失败")), "文件超过大小限制")
        self.assertEqual(
            attachment_reason(MediaError("cdn 请求失败，但缺少下载引用")), "微信附件服务报错")
        self.assertEqual(
            backend_reason(RuntimeError("websocket 连接失败：依赖缺失")), "后端连接中断")
        self.assertEqual(attachment_reason(MediaError("没有匹配的文本")), "附件处理失败")
        self.assertEqual(attachment_reason(FileNotFoundError(
            2, "系统找不到指定的文件。", "C:/Temp/a.txt")), "文件不存在或不可读")
        self.assertEqual(attachment_reason(MediaError(
            "不支持网络共享、设备路径、驱动器相对路径或 NTFS 附加数据流。")),
            "路径不受支持")

    async def test_desktop_session_info_uses_status_without_followup_note(self):
        self.bridge.state["thread"] = "01a13162-8d25-7883-ae2b-c70389dc9d58"
        self.bridge.backend.info = {
            "name": "微信附件", "model": "example", "effort": "high", "cwd": "/tmp/project"
        }
        self.bridge.backend.active = self.bridge.backend.new_turn(
            "thread", "turn", owned=False)
        self.bridge.prompt_fields = lambda: {"status": "运行中"}

        text = info(self.bridge, "已连接桌面会话")

        self.assertEqual(
            text,
            "已连接桌面会话\n```\n会话: 微信附件\nID: 01a13162\n状态: 运行中\n"
            "模型: example (High)\n目录: /tmp/project\n```")
        self.assertNotIn("/stop", text)
        self.assertNotIn("补充消息", text)

    async def test_status_shows_short_session_id(self):
        self.bridge.state["thread"] = "01a13162-8d25-7883-ae2b-c70389dc9d58"
        self.bridge.state["context"] = "context"
        await self.bridge.control("/s")
        self.assertIn("ID: 01a13162", self.bridge.state["outbox"][-1]["text"])

    async def test_a_missing_launcher_is_named_in_the_disconnect_log(self):
        # Without the reason a shared log only shows FileNotFoundError.
        with patch("codex_wechat.app.log_event") as log_event:
            self.bridge.backend_connection(
                self.bridge.backend, False,
                FileNotFoundError(2, "系统找不到指定的文件。", "codex.exe"))
        record = next(call for call in log_event.call_args_list
                      if call.args and call.args[0] == "backend.disconnected")
        self.assertEqual(record.kwargs["reason"], "启动器未就绪")
        self.assertEqual(record.kwargs["error_type"], "FileNotFoundError")

    async def test_restart_refuses_non_service_owner(self):
        owner = AsyncMock(side_effect=ValueError("not service owner"))
        with patch("codex_wechat.app.service_control.require_service_owner", owner):
            with self.assertRaises(UserFacingError) as raised:
                await self.bridge.restart_service()
        self.assertEqual(str(raised.exception), RESTART_NOT_SERVICE)

    async def test_session_list_hides_ids_and_raw_status(self):
        self.bridge.state["thread"] = "thread-current"
        self.bridge.state["context"] = "context"

        async def threads(cursor=None, search=None):
            return {"data": [
                {"id": "thread-current", "name": "当前会话",
                 "status": {"type": "active"}, "updatedAt": 0},
                {"id": "thread-working", "name": "Super-agent 调研",
                 "status": {"type": "active"}, "updatedAt": 0},
            ]}

        self.bridge.backend.list_threads = threads
        await handle(self.bridge, "/sessions", "")
        text = self.store.load("bridge")["outbox"][-1]["text"]
        self.assertTrue(text.startswith("```\n1."))
        self.assertIn("1. 当前会话  [当前  工作中", text)
        self.assertIn("2. Super-agent 调研  [工作中]", text)
        self.assertIn("```\n`/switch {编号、ID前缀或搜索词}`", text)
        self.assertNotIn("thread-current", text)
        self.assertNotIn("[active]", text)

    async def test_model_list_uses_compact_code_block(self):
        self.bridge.state["context"] = "context"

        async def models():
            return [
                {"model": "deepseek-flash", "supportedReasoningEfforts": [
                    {"reasoningEffort": "low"}, {"reasoningEffort": "high"},
                    {"reasoningEffort": "max"}]},
                {"model": "deepseek-v4-pro", "supportedReasoningEfforts": [
                    {"reasoningEffort": "high"}]},
            ]

        self.bridge.backend.models = models
        await handle(self.bridge, "/model", "")
        text = self.store.load("bridge")["outbox"][-1]["text"]
        self.assertTrue(text.startswith("```\n1."))
        self.assertIn("1. deepseek-flash", text)
        self.assertIn("2. deepseek-v4-pro", text)
        self.assertNotIn("Low", text)
        self.assertNotIn("High", text)
        self.assertIn("```\n`/m` `/model {编号或名称}`", text)
        self.assertNotIn("| 编号", text)

    async def test_sessions_numeric_pages_and_total_footer(self):
        self.bridge.state["context"] = "context"
        catalog = [{"id": str(i), "name": f"会话{i}"} for i in range(23)]

        async def threads(cursor=None, search=None):
            offset = int(cursor or 0)
            return {"data": catalog[offset:offset + 10],
                    "nextCursor": str(offset + 10) if offset + 10 < len(catalog) else None}

        self.bridge.backend.list_threads = threads
        await handle(self.bridge, "/sessions", "2")
        text = self.bridge.state["outbox"][-1]["text"]
        self.assertEqual(self.bridge.state["sessionChoices"], [str(i) for i in range(10, 20)])
        self.assertNotIn("2/3页", text.split("```")[1])
        self.assertTrue(text.endswith("`/sessions [页码|搜索词]` 当前2/3页"))
        self.assertNotIn("下一页", text)
        with self.assertRaises(UserFacingError):
            await handle(self.bridge, "/sessions", "4")
        with self.assertRaises(UserFacingError):
            await handle(self.bridge, "/sessions", "next")

    async def test_sessions_keyword_uses_native_search(self):
        self.bridge.state["context"] = "context"
        calls = []

        async def threads(cursor=None, search=None):
            calls.append((cursor, search))
            return {"data": [{"id": "target", "name": "微信重构"}]}

        self.bridge.backend.list_threads = threads
        await handle(self.bridge, "/sessions", "微信")
        text = self.bridge.state["outbox"][-1]["text"]
        self.assertEqual(calls, [(None, "微信")])
        self.assertIn("1. 微信重构", text)
        self.assertEqual(self.bridge.state["sessionChoices"], ["target"])
        self.assertEqual(self.bridge.state["sessionSearch"], "微信")

    async def test_switch_unique_search_result_activates(self):
        self.bridge.state.update(context="context", thread="current")
        self.bridge.backend.list_threads = AsyncMock(return_value={
            "data": [{"id": "target", "name": "微信重构"}]})
        self.bridge.backend.read_thread = AsyncMock(return_value={
            "id": "target", "status": {"type": "inactive"}})
        self.bridge.backend.prepare_thread = AsyncMock(return_value={
            "thread_id": "target",
            "result": {"thread": {"id": "target"}},
            "adopt": False,
            "desktop": False,
        })
        await handle(self.bridge, "/switch", "重构")
        self.bridge.backend.prepare_thread.assert_awaited_once_with(
            "target", {}, thread={"id": "target", "status": {"type": "inactive"}})
        self.assertEqual(self.bridge.state["thread"], "target")
        self.assertEqual(self.bridge.state["outbox"][-1]["text"].splitlines()[0],
                         "已切换会话")

    async def test_switch_accepts_unique_id_prefix(self):
        target = "01a13162-8d25-7883-ae2b-c70389dc9d58"
        self.bridge.state.update(context="context", thread="current")
        self.bridge.backend.list_threads = AsyncMock(return_value={
            "data": [{"id": target, "name": "目标会话"},
                     {"id": "deadbeef-0000-0000-0000-000000000000",
                      "name": "其他会话"}]})
        self.bridge.backend.read_thread = AsyncMock(return_value={
            "id": target, "status": {"type": "inactive"}})
        self.bridge.backend.prepare_thread = AsyncMock(return_value={
            "thread_id": target,
            "result": {"thread": {"id": target}},
            "adopt": False,
            "desktop": False,
        })
        await handle(self.bridge, "/switch", "01a13162")
        self.bridge.backend.prepare_thread.assert_awaited_once()
        self.assertEqual(self.bridge.state["thread"], target)

    async def test_switch_accepts_a_numeric_id_prefix(self):
        target = "1234abcd-0000-0000-0000-000000000000"
        self.bridge.state.update(context="context", thread="current")
        self.bridge.backend.list_threads = AsyncMock(return_value={
            "data": [{"id": target, "name": "数字前缀目标"},
                     {"id": "deadbeef-0000-0000-0000-000000000000",
                      "name": "其他会话"}]})
        self.bridge.backend.read_thread = AsyncMock(return_value={
            "id": target, "status": {"type": "inactive"}})
        self.bridge.backend.prepare_thread = AsyncMock(return_value={
            "thread_id": target,
            "result": {"thread": {"id": target}},
            "adopt": False,
            "desktop": False,
        })
        await handle(self.bridge, "/switch", "1234")
        self.bridge.backend.prepare_thread.assert_awaited_once()
        self.assertEqual(self.bridge.state["thread"], target)

    async def test_switch_ambiguous_id_prefix_lists_choices(self):
        first = "01a13162-8d25-7883-ae2b-c70389dc9d58"
        second = "01a1dead-0000-0000-0000-000000000000"
        self.bridge.state.update(context="context", thread="current")
        self.bridge.backend.list_threads = AsyncMock(return_value={
            "data": [{"id": first, "name": "会话一"},
                     {"id": second, "name": "会话二"}]})
        self.bridge.backend.prepare_thread = AsyncMock()
        await handle(self.bridge, "/switch", "01a1")
        self.bridge.backend.prepare_thread.assert_not_awaited()
        text = self.bridge.state["outbox"][-1]["text"]
        self.assertIn("1. 会话一", text)
        self.assertIn("2. 会话二", text)
        self.assertIn("找到 2 个匹配会话", text)
        self.assertEqual(self.bridge.state["sessionChoices"], [first, second])

    async def test_switch_id_prefix_truncation_asks_for_more_characters(self):
        ids = [f"01a1{i:04x}-0000-0000-0000-000000000000" for i in range(11)]
        self.bridge.state.update(context="context", thread="current")
        self.bridge.backend.list_threads = AsyncMock(return_value={
            "data": [{"id": identity, "name": f"会话 {i}"}
                     for i, identity in enumerate(ids)]})
        self.bridge.backend.prepare_thread = AsyncMock()
        await handle(self.bridge, "/switch", "01a1")
        self.bridge.backend.prepare_thread.assert_not_awaited()
        text = self.bridge.state["outbox"][-1]["text"]
        self.assertEqual(len(self.bridge.state["sessionChoices"]), 10)
        self.assertIn("共匹配 11 个会话", text)
        self.assertIn("仅显示前 10 个", text)
        self.assertNotIn("/sessions 01a1", text)

    async def test_hex_like_title_search_still_falls_back_to_title_search(self):
        target = "deadbeef-0000-0000-0000-000000000000"
        self.bridge.state.update(context="context", thread="current")

        async def threads(cursor=None, search=None):
            if search is None:
                return {"data": [{"id": "other", "name": "其他"}]}
            return {"data": [{"id": target, "name": "dead 笔记"}]}

        self.bridge.backend.list_threads = threads
        self.bridge.backend.read_thread = AsyncMock(return_value={
            "id": target, "status": {"type": "inactive"}})
        self.bridge.backend.prepare_thread = AsyncMock(return_value={
            "thread_id": target,
            "result": {"thread": {"id": target}},
            "adopt": False,
            "desktop": False,
        })
        await handle(self.bridge, "/switch", "dead")
        self.bridge.backend.prepare_thread.assert_awaited_once()

    async def test_switch_ambiguous_search_lists_choices_without_activating(self):
        self.bridge.state.update(context="context", thread="current")
        self.bridge.backend.list_threads = AsyncMock(return_value={"data": [
            {"id": "a", "name": "微信一"}, {"id": "b", "name": "微信二"}]})
        self.bridge.backend.prepare_thread = AsyncMock()
        await handle(self.bridge, "/switch", "微信")
        self.bridge.backend.prepare_thread.assert_not_awaited()
        text = self.bridge.state["outbox"][-1]["text"]
        self.assertIn("1. 微信一", text)
        self.assertIn("2. 微信二", text)
        self.assertIn("找到 2 个匹配会话", text)
        self.assertIn("`/switch {编号}`", text)
        self.assertEqual(self.bridge.state["sessionChoices"], ["a", "b"])

    async def test_switch_empty_search_reports_no_match(self):
        self.bridge.state.update(context="context", thread="current")
        self.bridge.backend.list_threads = AsyncMock(return_value={"data": []})
        for argument in ("不存在", "{4}"):
            with self.subTest(argument=argument):
                with self.assertRaises(UserFacingError) as raised:
                    await handle(self.bridge, "/switch", argument)
                self.assertIn("没有匹配", str(raised.exception))
                self.assertIn("例：`/switch 1`", str(raised.exception))

    async def test_switch_usage_errors_include_an_example(self):
        self.bridge.state.update(context="context", thread="current")
        with self.assertRaises(UserFacingError) as raised:
            await handle(self.bridge, "/switch", "")
        self.assertIn("例：`/switch 1`", str(raised.exception))

        self.bridge.state["sessionChoices"] = ["only"]
        with self.assertRaises(UserFacingError) as raised:
            await handle(self.bridge, "/switch", "2")
        self.assertIn("例：`/switch 1`", str(raised.exception))

    async def test_switch_truncated_matches_offer_search_outside_panel(self):
        self.bridge.state.update(context="context", thread="current")
        self.bridge.backend.list_threads = AsyncMock(return_value={"data": [
            {"id": str(i), "name": f"微信 {i}"} for i in range(11)]})
        self.bridge.backend.prepare_thread = AsyncMock()
        await handle(self.bridge, "/switch", "微信")
        text = self.bridge.state["outbox"][-1]["text"]
        self.bridge.backend.prepare_thread.assert_not_awaited()
        self.assertEqual(len(self.bridge.state["sessionChoices"]), 10)
        self.assertIn("仅显示前 10 条", text.split("```")[-1])
        self.assertIn("`/sessions 微信`", text.split("```")[-1])

    async def test_backend_arguments_and_built_in_labels_share_one_lowercase_form(self):
        self.bridge.state["context"] = "context"
        await handle(self.bridge, "/backend", "")
        text = self.store.load("bridge")["outbox"][-1]["text"]
        # config.default.json seeds lowercase labels, so the current backend and
        # the selectable names read the same. The panel used to show "Codex"
        # above a lowercase "codex | deepseek".
        self.assertIn("后端: codex", text)
        self.assertIn("可用: codex | deepseek", text)
        self.assertIn("```\n`/b` `/backend {名称}`", text)
        self.assertNotIn("后端: Codex", text)

    async def test_backend_query_reloads_persisted_definitions(self):
        path = Path(self.temp.name) / "config.json"
        path.write_text('{"backends": {"deepseek": {"command": ["old", "app-server"]}}}',
                        encoding="utf-8")
        self.bridge.settings.path = path
        reload_backend_definitions(self.bridge)
        self.assertEqual(self.bridge.backend_configs["deepseek"]["command"],
                         ["old", "app-server"])
        path.write_text('{"backends": {"deepseek": {"command": ["new", "app-server"]}}}',
                        encoding="utf-8")
        self.bridge.state["context"] = "context"
        await handle(self.bridge, "/backend", "")
        self.assertEqual(self.bridge.backend_configs["deepseek"]["command"],
                         ["new", "app-server"])

    async def test_backend_switch_reconnects_a_changed_current_definition(self):
        path = Path(self.temp.name) / "config.json"
        path.write_text('{"backends": {"codex": {"useSystemProxy": true}}}',
                        encoding="utf-8")
        self.bridge.settings.path = path
        reload_backend_definitions(self.bridge)
        path.write_text('{"backends": {"codex": {"useSystemProxy": false}}}',
                        encoding="utf-8")
        with patch("codex_wechat.commands.switch", AsyncMock()) as switch:
            await handle(self.bridge, "/backend", "codex")
        switch.assert_awaited_once_with(self.bridge, "codex", force=True)

    async def test_custom_backend_is_data_driven_and_display_label_is_optional(self):
        config = {"backends": {"kimi": {"label": "Kimi", "command": ["fake-kimi", "app-server"]}}}
        bridge = Bridge(self.store, {"user": "owner"}, config)
        self.assertEqual(bridge.backend_name, "kimi")
        # A label is shown verbatim; the built-in ones are lowercase so the panel
        # agrees with the names `/backend` accepts.
        self.assertEqual(bridge.prompt_fields()["backend"], "Kimi")
        self.assertEqual(backend_label("kimi", config["backends"]), "Kimi")
        self.assertEqual(backend_label("kimi", {"kimi": {}}), "kimi")

    async def test_custom_backend_requires_an_explicit_command(self):
        with self.assertRaisesRegex(ValueError, "command 必须配置"):
            definitions({"backends": {"kimi": {"label": "Kimi"}}})

    async def test_missing_backend_command_fails_without_switching(self):
        self.bridge.backend_configs["missing"] = {
            "command": ["definitely-not-a-real-codex-binary", "app-server"],
            "sharedDaemon": False,
        }
        self.bridge.state["context"] = "context"
        await dispatch(self.bridge, "/backend missing")
        self.assertEqual(self.bridge.backend_name, "codex")
        self.assertIn("切换后端失败", self.bridge.state["outbox"][-1]["text"])

    def test_backend_seed_adds_defaults_when_config_has_none(self):
        config = {}
        self.assertTrue(ensure_backend_config(config))
        # config.default.json filters entries by platform, so compare with the
        # template actually applicable here instead of naming a backend.
        self.assertEqual(config["backends"], default_backends())

    def test_backend_migration_fills_known_backend_defaults(self):
        config = {"backends": {"codex": {"useSystemProxy": False}}}
        self.assertTrue(ensure_backend_config(config))
        self.assertEqual(config["backends"]["codex"]["command"],
                         ["codex", "app-server"])
        self.assertEqual(config["backends"]["codex"]["label"], "codex")
        # An explicit setting must survive the merge.
        self.assertFalse(config["backends"]["codex"]["useSystemProxy"])

    async def test_stop_during_input_preflight_never_starts_turn(self):
        backend = CodexBackend({"sharedDaemon": False}, self.temp.name, lambda _: None)
        preparing, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def inputs(*args):
            preparing.set()
            await release.wait()
            return []

        async def rpc(method, params):
            calls.append(method)
            raise AssertionError("No RPC should run after stopping preflight")

        backend.input_items, backend.rpc = inputs, rpc
        task = asyncio.create_task(backend.run("thread", "image task"))
        await preparing.wait()
        self.assertTrue(await backend.stop())
        release.set()
        self.assertIn("未启动", (await task).text)
        self.assertEqual(calls, [])

    async def test_desktop_events_deliver_once_without_owning_turn(self):
        backend = self.bridge.backend
        backend.selected_thread = "desktop-thread"
        results = []
        backend.on_external_result = results.append
        backend.event({"method": "turn/started", "params": {"threadId": "desktop-thread",
                       "turn": {"id": "desktop-turn"}}})
        self.assertFalse(backend.active["owned"])
        backend.event({"method": "item/completed", "params": {"threadId": "desktop-thread",
            "turnId": "desktop-turn", "item": {"type": "agentMessage", "phase": "final_answer",
                                                  "text": "desktop result"}}})
        done = {"method": "turn/completed", "params": {"threadId": "desktop-thread",
                "turn": {"id": "desktop-turn", "status": "completed"}}}
        backend.event(done)
        backend.event(done)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].text, "desktop result")
        self.assertTrue(results[0].completed)
        self.assertIsNone(backend.active)

    async def test_sparse_rate_limit_updates_merge_without_thread(self):
        backend = self.bridge.backend
        backend.event({"method": "account/rateLimits/updated", "params": {
            "rateLimits": {"primary": {"usedPercent": 20, "windowDurationMins": 300}}
        }})
        backend.event({"method": "account/rateLimits/updated", "params": {
            "rateLimits": {"secondary": {"usedPercent": 40, "windowDurationMins": 10080}}
        }})
        snapshot = backend.account_limits["rateLimits"]
        self.assertEqual(snapshot["primary"]["usedPercent"], 20)
        self.assertEqual(snapshot["secondary"]["usedPercent"], 40)

    async def test_approval_prompt_is_structured_instead_of_json(self):
        backend = self.bridge.backend
        backend.selected_thread = "thread"
        backend.active = backend.new_turn("thread", "turn", owned=True)
        notifications = []
        backend.notify = notifications.append
        await backend.server_request({
            "id": 7,
            "method": "item/commandExecution/requestApproval",
            "params": {
                "threadId": "thread",
                "turnId": "turn",
                "itemId": "item",
                "command": "git push origin main",
                "cwd": "~/projects/codex-wechat",
                "reason": "Agent 请求推送当前分支",
            },
        })
        prompt = notifications[-1]
        self.assertIn("审批: 执行命令", prompt)
        self.assertIn("命令: git push origin main", prompt)
        self.assertIn("/approve ", prompt)
        self.assertNotIn('"threadId"', prompt)

    async def test_oversized_approval_is_rejected_with_clear_prompt(self):
        backend = self.bridge.backend
        backend.selected_thread = "thread"
        backend.active = backend.new_turn("thread", "turn", owned=True)
        notifications, writes = [], []

        async def write(message):
            writes.append(message)

        backend.notify, backend.write = notifications.append, write
        await backend.server_request({
            "id": 8,
            "method": "item/commandExecution/requestApproval",
            "params": {
                "threadId": "thread",
                "turnId": "turn",
                "command": "x" * 1300,
                "cwd": "~/projects/codex-wechat",
            },
        })
        self.assertIn("审批内容过长", notifications[-1])
        self.assertFalse(backend.pending)
        self.assertEqual(writes[0]["error"]["code"], -32601)

    async def test_definite_steer_rejection_preserves_active_turn(self):
        backend = self.bridge.backend
        current = backend.new_turn("thread", "review-turn", owned=False)
        backend.active = current

        async def reject(*args):
            raise CodexRpcError(-32600, "active turn is not steerable")

        backend.rpc = reject
        self.assertFalse(await backend.steer("thread", "review-turn", "follow-up"))
        self.assertIs(backend.active, current)
        self.assertFalse(current["done"].done())

    async def test_media_reads_only_the_current_workspace(self):
        with tempfile.TemporaryDirectory() as workspace_dir:
            workspace = Path(workspace_dir)
            hidden = workspace / ".env"
            hidden.write_text("hidden-but-in-scope")
            asset = self.bridge.media.snapshot(str(hidden), workspace)
            self.assertEqual(Path(asset["path"]).read_text(), "hidden-but-in-scope")
            with tempfile.TemporaryDirectory() as sibling:
                outside = Path(sibling) / "private.txt"
                outside.write_text("private")
                with self.assertRaises(MediaError):
                    self.bridge.media.snapshot(str(outside), workspace)
            state_file = self.bridge.store.root / "bridge.json"
            state_file.write_text("private")
            with self.assertRaises(MediaError):
                self.bridge.media.snapshot(str(state_file), workspace)
