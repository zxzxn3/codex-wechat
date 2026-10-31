from errors import UserFacingError, attachment_reason, backend_reason
import asyncio
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from agent_wechat import Bridge, parse_control
from backend import CodexBackend, CodexRpcError
from media import OUTPUT_HINT, MediaError
from latency import Latency
from command_specs import HELP
from commands import handle
from configuration import DEFAULT_ACK_TEMPLATE, processing_template
from prompts import (
    PING_REPLY,
    RESTART_NOT_SERVICE,
    render_ack,
    session_summary,
    setting_summary,
    settings_summary,
    status_summary,
)
from storage import Store
from wechat import trusted_url


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.bridge = Bridge(self.store, {"user": "owner"}, {})

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
        restarted = Bridge(self.store, {"user": "owner"}, {})
        await restarted.receive({"msgs": [self.message()]})
        self.assertEqual(len(restarted.state["inbox"]), 1)

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
        self.assertEqual(parse_control("/send /tmp/a b.txt"),
                         ("/send", "/tmp/a b.txt"))
        self.assertEqual(parse_control("   "), ("", ""))

    async def test_private_state_and_utf8_chunks(self):
        self.bridge.state["context"] = "context"
        self.bridge.reply("汉" * 2500)
        entries = self.store.load("bridge")["outbox"]
        self.assertEqual("".join(x["text"] for x in entries), "汉" * 2500)
        self.assertTrue(all(len(x["text"].encode()) <= 3000 for x in entries))
        self.assertEqual(os.stat(Path(self.temp.name) / "bridge.json").st_mode & 0o777, 0o600)

    async def test_empty_completion_notice_is_disabled(self):
        self.bridge.state["context"] = "context"
        await self.bridge.deliver_answer("answer", notify_completion=True)
        texts = [entry.get("text") for entry in self.store.load("bridge")["outbox"]]
        self.assertEqual(texts, ["answer"])

    async def test_output_hint_is_concise_origin_marker(self):
        self.assertEqual(OUTPUT_HINT, "\n\n[User from WeChat.]")

    async def test_stdio_backend_final_only_and_resume(self):
        backend = CodexBackend({"command": [sys.executable,
            str(Path(__file__).with_name("fake_server.py"))]}, self.temp.name, lambda _: None)
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
        entry = self.store.load("bridge")["outbox"][-1]
        self.assertEqual(entry["text"], "WeChat ─120/50ms─ Bridge ─unavailable─ Provider")
        self.assertNotIn("ping", entry)
        self.assertEqual(len(self.store.load("bridge")["outbox"]), 1)
        self.assertFalse(self.bridge.backend_started)

    async def test_settings_remain_available_without_backend(self):
        self.bridge.state["context"] = "context"
        await self.bridge.control("/settings")
        self.assertIn("附件上限", self.store.load("bridge")["outbox"][-1]["text"])
        self.assertFalse(self.bridge.backend_started)

    async def test_latency_shows_only_wechat_channel(self):
        stats = Latency()
        stats.sends.append(50)
        values = stats.values(120)
        self.assertEqual(values, {"发送延迟": "120", "接收延迟": "50"})
        self.assertEqual(render_ack(DEFAULT_ACK_TEMPLATE, values), "120/50ms")
        self.assertEqual(render_ack(PING_REPLY, values), "WeChat ─120/50ms")
        self.assertEqual(stats.values(), {"发送延迟": "-", "接收延迟": "50"})

    async def test_ack_text_controls_notice_without_switch(self):
        self.bridge.state["context"] = "context"
        self.bridge.state["processingNotice"] = "{发送延迟}|{接收延迟}ms"
        self.bridge.latency.sends.append(80)
        self.bridge.acknowledge({"ingress_ms": 120})
        self.assertEqual(self.store.load("bridge")["outbox"][-1]["text"], "120|80ms")

        await self.bridge.control("/ack text")
        self.assertEqual(processing_template(self.bridge.config, self.bridge.state), "")
        before = len(self.bridge.state["outbox"])
        self.bridge.acknowledge({"ingress_ms": 120})
        self.assertEqual(len(self.bridge.state["outbox"]), before)

    async def test_legacy_latency_template_uses_split_default(self):
        for legacy in ("{latency}", "::{发送延迟}/{接收延迟}ms::"):
            config = {"processingNotice": {"enabled": False, "text": legacy}}
            self.assertEqual(processing_template(config, {}), DEFAULT_ACK_TEMPLATE)
        self.assertEqual(processing_template({}, {"processingNotice": ""}), "")

    async def test_user_prompt_formats(self):
        self.assertEqual(session_summary(
            "已切换会话", name="微信附件", model="example", effort="high", cwd="~/project"),
            "已切换会话\n```\n会话: 微信附件\n模型: example (High)\n目录: ~/project\n```")
        self.assertEqual(setting_summary(model="example", effort="high", cwd="~/project", field="model"),
                         "模型: example (High)")
        self.assertEqual(setting_summary(model="example", effort="high", cwd="~/project", field="effort"),
                         "强度: High")
        self.assertEqual(setting_summary(model="example", effort="high", cwd="~/project", field="cwd"),
                         "目录: ~/project")
        self.assertEqual(settings_summary(notice=DEFAULT_ACK_TEMPLATE, attachment_mib=20),
                         "```\n处理提示: {发送延迟}/{接收延迟}ms\n完成提示: 关闭\n附件上限: 20 MiB\n```")
        status = status_summary(status="运行中", queued=2, pending=0, failed=0,
            backend="Codex", name="微信附件", model="example", effort="high", cwd="~/project",
            connection="本地后台", provider="openai",
            sandbox={"type": "workspaceWrite"}, approval="on-request", reviewer="user",
            collaboration={"mode": "default"},
            usage={"last": {"totalTokens": 12345},
                   "total": {"totalTokens": 34567, "inputTokens": 30000, "outputTokens": 4567},
                   "modelContextWindow": 200000},
            rate_limits={"rateLimits": {"primary": {"usedPercent": 10, "windowDurationMins": 300}}})
        for expected in ("状态: 运行中", "待办: 2", "后端: Codex", "模型: example (High)",
                         "权限: 工作区可写", "协作: 默认", "服务: 本地后台",
                         "Token: 34.6K total\n       30K input + 4.6K output",
                         "上下文: 剩余 94% (12.3K / 200K)", "限额: 5小时剩余 90%"):
            self.assertIn(expected, status)
        for unwanted in ("审批: 0", "发送失败: 0", "Session", "·", "->", "→"):
            self.assertNotIn(unwanted, status)
        self.assertTrue(HELP.startswith("`/status` 当前状态"))
        for command in ("/status", "/switch {编号或ID}", "/backend {名称}", "/approve", "/sendimage"):
            self.assertIn(command, HELP)
        self.assertNotIn("```", HELP)
        self.assertEqual(backend_reason(RuntimeError("WebSocket 握手失败")), "后端连接中断")
        self.assertEqual(attachment_reason(MediaError("附件超过本地大小限制")), "文件超过大小限制")

    async def test_restart_refuses_non_service_owner(self):
        owner = AsyncMock(side_effect=ValueError("not service owner"))
        with patch("agent_wechat.service_control.require_service_owner", owner):
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
        self.assertIn("```\n`/switch {编号或ID}`", text)
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
        self.assertIn("```\n`/model {编号或名称}`", text)
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
        self.assertTrue(text.endswith("`/sessions {页码}` 当前2/3页"))
        self.assertNotIn("下一页", text)
        with self.assertRaises(UserFacingError):
            await handle(self.bridge, "/sessions", "4")
        with self.assertRaises(UserFacingError):
            await handle(self.bridge, "/sessions", "next")

    async def test_backend_arguments_are_lowercase(self):
        self.bridge.state["context"] = "context"
        await handle(self.bridge, "/backend", "")
        text = self.store.load("bridge")["outbox"][-1]["text"]
        self.assertIn("可用: codex | deepseek", text)
        self.assertIn("```\n`/backend {名称}`", text)
        self.assertNotIn("切换：`/backend DeepSeek`", text)

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
                "cwd": "~/projects/agent-wechat",
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
                "cwd": "~/projects/agent-wechat",
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

    async def test_automatic_media_cannot_read_outside_workspace(self):
        workspace = Path(self.temp.name) / "workspace"
        workspace.mkdir()
        with tempfile.TemporaryDirectory() as sibling:
            outside = Path(sibling) / "private.txt"
            outside.write_text("private")
            with self.assertRaises(MediaError):
                self.bridge.media.snapshot(str(outside), workspace, automatic=True)
            # An explicit user /send remains a separate, intentional operation.
            asset = self.bridge.media.snapshot(str(outside), workspace)
            self.assertEqual(Path(asset["path"]).read_text(), "private")
