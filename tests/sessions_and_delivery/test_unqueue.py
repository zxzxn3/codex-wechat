"""Cancel waiting queue inputs without touching claimed or steered work."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock

from codex_wechat.app import Bridge
from codex_wechat.command_specs import REGISTRY, command_lane, help_text
from codex_wechat.commands import dispatch
from codex_wechat.records import InputPhase
from codex_wechat.storage import Store


class UnqueueTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "state")
        self.bridge = Bridge(self.store, {"user": "owner"}, {})
        self.bridge.state["context"] = "context"
        self.bridge.ensure_backend = AsyncMock()

    async def asyncTearDown(self):
        self.store.lock.close()
        self.temp.cleanup()

    async def test_removes_last_waiting_queue_and_persists_cancellation(self):
        self.bridge.state["inbox"] = [
            {"id": "first", "mode": "queue"},
            {"id": "last", "mode": "queue", "phase": "preparing"},
            {"id": "steer", "mode": "steer"},
        ]
        await dispatch(self.bridge, "/unqueue")
        saved = self.store.load("bridge")
        self.assertEqual([job["id"] for job in saved["inbox"]], ["first", "steer"])
        self.assertEqual(saved["inputRecords"][-1]["id"], "last")
        self.assertEqual(saved["inputRecords"][-1]["phase"], InputPhase.CANCELLED)
        self.assertEqual(saved["outbox"][-1]["text"], "已撤回，剩余 2 条。")
        self.bridge.ensure_backend.assert_not_called()

    async def test_does_not_cancel_current_or_submitted_work(self):
        self.bridge.running = True
        current = {"id": "current", "mode": "queue", "phase": "preparing"}
        self.bridge.state["currentInput"] = current
        self.bridge.state["inbox"] = [
            {"id": "waiting", "mode": "queue"},
            {"id": "dispatching", "mode": "queue", "phase": "dispatching"},
            {"id": "accepted", "mode": "queue", "phase": "accepted"},
        ]
        await dispatch(self.bridge, "/unqueue")
        self.assertIs(self.bridge.state["currentInput"], current)
        self.assertTrue(self.bridge.running)
        self.assertEqual([job["id"] for job in self.bridge.state["inbox"]],
                         ["dispatching", "accepted"])

    async def test_no_waiting_queue_leaves_steer_untouched(self):
        self.bridge.state["inbox"] = [{"id": "steer"}]
        await dispatch(self.bridge, "/unqueue")
        self.assertEqual(self.bridge.state["inbox"], [{"id": "steer"}])
        self.assertEqual(self.bridge.state["outbox"][-1]["text"], "没有可撤回的排队消息。")

    async def test_zero_and_one_remaining_use_correct_count(self):
        self.bridge.state["inbox"] = [{"mode": "queue"}, {"mode": "queue"}]
        await dispatch(self.bridge, "/unqueue")
        self.assertEqual(self.bridge.state["outbox"][-1]["text"], "已撤回，剩余 1 条。")
        await dispatch(self.bridge, "/unqueue")
        self.assertEqual(self.bridge.state["outbox"][-1]["text"], "已撤回，剩余 0 条。")

    async def test_urgent_local_command_rejects_arguments(self):
        self.assertEqual(command_lane("/unqueue", ""), "urgent")
        self.assertFalse(REGISTRY["/unqueue"].needs_backend)
        self.assertIn("`/unqueue`", help_text())
        self.bridge.state["inbox"] = [{"mode": "queue"}]
        await dispatch(self.bridge, "/unqueue extra")
        self.assertEqual(len(self.bridge.state["inbox"]), 1)
        self.assertIn("用法", self.bridge.state["outbox"][-1]["text"])
        self.bridge.ensure_backend.assert_not_called()
