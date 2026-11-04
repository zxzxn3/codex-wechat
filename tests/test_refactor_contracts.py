"""Small regression contracts; only synthetic events and temporary files."""
import asyncio
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch, AsyncMock

from backend import CodexBackend
from command_specs import command_lane
from prompts import ping_channel, prompt_text
from usage import UsageCache


class StabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_duplicate_final_item_is_collected_once(self):
        backend = CodexBackend({}, "/tmp", lambda _: None)
        backend.selected_thread = "t"
        backend.active = backend.new_turn("t", "turn", owned=False)
        results = []
        backend.on_external_result = results.append
        event = {"method": "item/completed", "params": {"threadId": "t", "turnId": "turn",
                 "item": {"id": "answer", "type": "agentMessage", "text": "hello", "phase": "final_answer"}}}
        backend.event(event)
        backend.event(event)
        backend.event({"method": "turn/completed", "params": {"threadId": "t",
                       "turn": {"id": "turn", "status": "completed"}}})
        self.assertEqual([result.text for result in results], ["hello"])

    def test_restart_and_media_have_independent_lanes(self):
        self.assertEqual(command_lane("/restart", ""), "local")
        self.assertEqual(command_lane("/get", "file"), "media")

    def test_ping_missing_sample_has_no_unit(self):
        self.assertEqual(ping_channel({"ingress_ms": "120", "egress_ms": "-"}), "微信 ─120ms/-")
        self.assertEqual(ping_channel({"ingress_ms": "-", "egress_ms": "-"}), "微信 ─-/-")

    def test_usage_cache_reads_only_changed_files(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "thread.jsonl"
            path.write_text("first")
            cache = UsageCache()
            with patch("usage.stored_usage", side_effect=[{"total": 1}, {"total": 2}, None]) as read:
                self.assertEqual(cache.read(path, "thread"), {"total": 1})
                self.assertEqual(cache.read(path, "thread"), {"total": 1})
                self.assertEqual(read.call_count, 1)
                path.write_text("second revision")
                self.assertEqual(cache.read(path, "thread"), {"total": 2})
                cache.clear()
                self.assertIsNone(cache.read(path, "thread"))
                path.unlink()
                self.assertIsNone(cache.read(path, "thread"))


class CompletionStateTests(unittest.IsolatedAsyncioTestCase):
    async def test_completion_uses_finished_state_without_clearing_live_flags(self):
        from codex_wechat import Bridge
        from storage import Store
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root) / "state")
            try:
                bridge = Bridge(store, {"user": "owner"}, {"completionNotice": "{status}"})
                bridge.state["context"] = "context"
                bridge.backend_started = True
                bridge.backend.proc = SimpleNamespace(returncode=None)
                bridge.backend.reader = asyncio.get_running_loop().create_future()
                bridge.running = True
                await bridge.deliver_answer("answer", notify_completion=True)
                self.assertEqual(bridge.state["outbox"][-1]["text"], "空闲")
                self.assertTrue(bridge.running)
                self.assertEqual(bridge.prompt_fields()["status"], "运行中")
            finally:
                store.lock.close()


class CommandRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_command_does_not_connect(self):
        from commands import dispatch, HANDLERS
        from command_specs import COMMANDS
        self.assertEqual(set(HANDLERS), {spec.name for spec in COMMANDS if spec.route != "input"})
        bridge = SimpleNamespace(reply=lambda _: None, ensure_backend=AsyncMock(),
                                 prompt=prompt_text)
        for command in ("/compact bad", "/new bad", "/switch", "/sessions next", "/sessions 0", "/missing"):
            await dispatch(bridge, command)
        bridge.ensure_backend.assert_not_awaited()
