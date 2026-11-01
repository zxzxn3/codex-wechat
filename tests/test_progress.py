"""Visible interim replies use complete native items, never model/tool calls."""
import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock

from agent_wechat import Bridge
from backend import DeferTurn
from codex_transport import CodexRpcError
from records import AgentMessage, InputPhase
from storage import Store


class ProgressTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "state")
        self.bridge = Bridge(self.store, {"user": "owner"}, {"completionNotice": "结束"})
        self.bridge.state.update(context="context", thread="thread", currentInput={
            "id": "job", "phase": InputPhase.ACCEPTED})
        self.backend = self.bridge.backend
        self.backend.selected_thread = "thread"
        self.backend.active = self.backend.new_turn("thread", "turn", owned=True)
        self.bridge.running = True

    async def asyncTearDown(self):
        self.store.lock.close()
        self.temp.cleanup()

    def item(self, phase="commentary", text="正在检查日志。", identity="progress", **overrides):
        params = {"threadId": "thread", "turnId": "turn", "item": {
            "id": identity, "type": "agentMessage", "phase": phase, "text": text}}
        params.update(overrides)
        return {"method": "item/completed", "params": params}

    def texts(self):
        return [entry.get("text") for entry in self.bridge.state["outbox"]]

    def finish(self, status="completed"):
        self.backend.event({"method": "turn/completed", "params": {
            "threadId": "thread", "turn": {"id": "turn", "status": status}}})

    async def test_progress_is_persisted_before_turn_end_without_retiring_input(self):
        current = self.backend.active
        self.backend.event(self.item())
        self.assertEqual(self.texts(), ["正在检查日志。"])
        self.assertFalse(current["done"].done())
        self.assertEqual(self.store.load("bridge")["currentInput"]["phase"], "accepted")
        self.assertTrue(self.bridge.running)
        self.assertEqual(self.store.load("bridge")["outbox"][0]["text"], self.texts()[0])

    async def test_partial_and_final_keep_order_without_duplicating_interim(self):
        current = self.backend.active
        self.backend.event(self.item())
        self.backend.event(self.item("partial_answer", "已定位到配置。", "partial"))
        self.backend.event(self.item("final_answer", "已完成修复。", "final"))
        self.assertEqual(self.texts(), ["正在检查日志。", "已定位到配置。"])
        self.finish()
        result = await current["done"]
        self.assertEqual(result.text, "已完成修复。")
        await self.bridge.deliver_answer(result)
        self.assertEqual(self.texts(), ["正在检查日志。", "已定位到配置。", "已完成修复。", "结束"])
        self.assertNotIn("currentInput", self.bridge.state)

    async def test_duplicate_late_wrong_turn_and_wrong_thread_items_are_ignored(self):
        event = self.item()
        self.backend.event(event)
        self.backend.event(event)
        self.backend.event(self.item(identity="wrong", turnId="other"))
        self.backend.event(self.item(identity="wrong-thread", threadId="other"))
        self.assertEqual(len(self.texts()), 1)
        self.finish()
        self.backend.event(self.item(identity="late"))
        self.assertEqual(len(self.texts()), 1)

    async def test_reasoning_tools_deltas_and_unfinished_items_are_not_forwarded(self):
        for kind in ("reasoning", "commandExecution", "plan", "mcpToolCall"):
            event = self.item(identity=kind)
            event["params"]["item"]["type"] = kind
            self.backend.event(event)
        for method in ("item/agentMessage/delta", "item/started"):
            event = self.item(identity=method)
            event["method"] = method
            self.backend.event(event)
        self.assertFalse(self.texts())

    async def test_progress_attachments_are_not_snapshotted_and_only_text_is_sent(self):
        self.bridge.media.snapshot = AsyncMock(side_effect=AssertionError("must not snapshot"))
        self.backend.event(self.item(text='处理中\nWECHAT_MEDIA: {"path":"/private","kind":"file"}'))
        self.assertEqual(self.texts(), ["处理中"])
        self.bridge.media.snapshot.assert_not_called()

    async def test_source_epoch_and_previous_backend_are_filtered(self):
        message = AgentMessage(self.backend.source(), "turn", "item", "旧消息", "/tmp", "commentary")
        self.backend.selection_epoch += 1
        self.bridge.output.deliver_message(message)
        self.assertFalse(self.texts())
        previous = self.backend
        self.bridge.backend = self.bridge.make_backend(self.bridge.backend_name)
        previous.on_message(AgentMessage(previous.source(), "turn", "item", "旧后端", "/tmp", "commentary"))
        self.assertFalse(self.texts())

    async def test_progress_only_turn_has_no_false_empty_result_notice(self):
        current = self.backend.active
        self.backend.event(self.item("partial_answer", "已经查到结果。"))
        self.finish()
        result = await current["done"]
        self.assertEqual(result.text, "")
        await self.bridge.deliver_answer(result)
        self.assertEqual(self.texts(), ["已经查到结果。", "结束"])

    async def test_legacy_unphased_text_still_uses_final_delivery(self):
        current = self.backend.active
        self.backend.event(self.item(None, "旧协议回答。"))
        self.assertFalse(self.texts())
        self.finish()
        self.assertEqual((await current["done"]).text, "旧协议回答。")

    async def test_desktop_progress_uses_same_delivery(self):
        self.backend.active = self.backend.new_turn("thread", "turn", owned=False)
        self.backend.event(self.item())
        self.assertEqual(self.texts(), ["正在检查日志。"])

    async def test_early_progress_waits_for_our_start_response(self):
        backend = self.backend
        backend.active = None
        backend.refresh_runtime = AsyncMock()
        backend.input_items = AsyncMock(return_value=[])

        async def start(method, params):
            backend.event(self.item())
            self.assertFalse(self.texts())
            backend.event(self.item("final_answer", "完成", "final"))
            self.finish()
            return {"turn": {"id": "turn"}}

        backend.rpc = start
        result = await backend.run("thread", "进度如何")
        self.assertEqual(result.text, "完成")
        self.assertEqual(self.texts(), ["正在检查日志。"])

    async def test_desktop_progress_early_in_rejected_start_is_replayed(self):
        backend = self.backend
        backend.active = None
        backend.shared = True
        backend.selected_thread = "thread"
        reads = 0

        async def read_thread(_thread):
            nonlocal reads
            reads += 1
            return {"id": "thread",
                    "status": {"type": "inactive" if reads == 1 else "active"}}

        async def rpc(method, params):
            if method == "thread/turns/list":
                return {"data": [{"id": "desktop-turn", "status": "inProgress"}]}
            if method == "turn/start":
                backend.event({"method": "turn/started", "params": {
                    "threadId": "thread", "turn": {"id": "desktop-turn"}}})
                backend.event(self.item(
                    turnId="desktop-turn", identity="desktop-progress",
                    text="桌面进度"))
                raise CodexRpcError(
                    -32600, "turn already active", "turn/start", definite=True)
            raise AssertionError((method, params))

        backend.read_thread = read_thread
        backend.rpc = rpc
        with self.assertRaises(DeferTurn):
            await backend.run("thread", "我们的消息")
        self.assertEqual(self.texts(), ["桌面进度"])
