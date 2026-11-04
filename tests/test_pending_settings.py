"""Model and effort asked for mid-turn land on the next turn instead of a barrier."""
import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock

from codex_wechat import Bridge
from codex_transport import CodexRpcError
from commands import (_change_cwd, _change_effort, _change_model, apply_pending_settings,
                      command_new, command_switch)
from errors import UserFacingError
from storage import Store


MODELS = [{"model": "gpt-5.2", "defaultReasoningEffort": "medium",
           "supportedReasoningEfforts": [{"reasoningEffort": "low"},
                                         {"reasoningEffort": "high"}]}]


class PendingSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "state")
        self.bridge = Bridge(self.store, {"user": "owner"}, {})
        self.bridge.state["thread"] = "t"
        self.bridge.loaded = True
        self.bridge.ensure_backend = AsyncMock()
        self.bridge.reply = Mock()
        self.bridge.backend.read_thread = AsyncMock(return_value={"status": {"type": "idle"}})
        self.bridge.backend.thread = AsyncMock(return_value="t")
        self.bridge.backend.release_thread = AsyncMock(return_value=True)
        self.bridge.backend.models = AsyncMock(return_value=MODELS)

    async def asyncTearDown(self):
        self.store.lock.close()
        self.temp.cleanup()

    async def test_model_change_during_a_turn_is_recorded_not_resumed(self):
        self.bridge.running = True
        await _change_model(self.bridge, "/model", "gpt-5.2")
        self.bridge.backend.thread.assert_not_awaited()
        self.assertEqual(self.bridge.state["pendingSettings"],
                         {"model": "gpt-5.2", "effort": "medium"})
        self.assertIn("下一条消息", self.bridge.reply.call_args.args[0])

    async def test_effort_change_during_a_turn_names_the_requested_effort(self):
        self.bridge.running = True
        self.bridge.backend.info = {"model": "gpt-5.2", "effort": "low"}
        await _change_effort(self.bridge, "/effort", "high")
        self.bridge.backend.thread.assert_not_awaited()
        self.assertEqual(self.bridge.state["pendingSettings"], {"effort": "high"})
        self.assertIn("High", self.bridge.reply.call_args.args[0])

    async def test_idle_change_still_resumes_immediately(self):
        self.bridge.backend.info = {"model": "gpt-5.2", "effort": "medium", "cwd": "/cwd"}
        await _change_effort(self.bridge, "/effort", "high")
        self.assertEqual(self.bridge.backend.thread.await_args.args, ("t", {"effort": "high"}))
        self.assertNotIn("pendingSettings", self.bridge.state)

    async def test_pending_setting_applies_and_clears_on_the_next_turn(self):
        self.bridge.state["pendingSettings"] = {"effort": "high"}
        await apply_pending_settings(self.bridge)
        self.assertEqual(self.bridge.backend.thread.await_args.args, ("t", {"effort": "high"}))
        self.assertNotIn("pendingSettings", self.bridge.state)

    async def test_failed_apply_keeps_the_setting_for_the_next_turn(self):
        self.bridge.state["pendingSettings"] = {"effort": "high"}
        self.bridge.backend.thread = AsyncMock(
            side_effect=CodexRpcError(-32603, "resume failed"))
        await apply_pending_settings(self.bridge)
        self.assertEqual(self.bridge.state["pendingSettings"], {"effort": "high"})
        self.assertIn("High", self.bridge.reply.call_args.args[0])

    async def test_switching_sessions_drops_the_pending_choice(self):
        self.bridge.state["pendingSettings"] = {"effort": "high"}
        self.bridge.backend.thread = AsyncMock(return_value="created")
        await command_new(self.bridge, "/new", "")
        self.assertNotIn("pendingSettings", self.bridge.state)

    async def test_a_failed_switch_keeps_the_pending_choice(self):
        self.bridge.state["pendingSettings"] = {"effort": "high"}
        self.bridge.backend.read_thread = AsyncMock(side_effect=CodexRpcError(-32603, "boom"))
        target = "01a1a470-a6bd-7e70-8727-0be3f2178e9d"
        with self.assertRaises(CodexRpcError):
            await command_switch(self.bridge, "/switch", target)
        self.assertEqual(self.bridge.state["pendingSettings"], {"effort": "high"})

    async def test_pending_setting_lands_before_the_next_turn(self):
        bridge = self.bridge
        bridge.loaded = True
        bridge.ensure_backend = AsyncMock()
        bridge.media.incoming = AsyncMock(return_value=[])
        bridge.state["pendingSettings"] = {"effort": "high"}
        bridge.state["inbox"] = [{"id": "one", "clientId": "client", "text": "one",
                                  "phase": "preparing", "attachments": []}]
        seen = []
        async def run(thread_id, text, **kwargs):
            seen.append((thread_id, dict(bridge.state)))
            return None
        bridge.backend.run = run
        task = asyncio.create_task(bridge.worker())
        try:
            for _ in range(200):
                if seen:
                    break
                await asyncio.sleep(0.01)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(seen[0][0], "t")
        self.assertNotIn("pendingSettings", seen[0][1])
        self.assertEqual(self.bridge.backend.thread.await_args.args, ("t", {"effort": "high"}))

    async def test_a_directory_change_still_waits_for_the_barrier(self):
        self.bridge.running = True
        with self.assertRaises(UserFacingError) as raised:
            await _change_cwd(self.bridge, "/cwd", "/tmp")
        self.assertEqual(str(raised.exception), self.bridge.prompt("BUSY_CHANGE"))
        self.assertNotIn("pendingSettings", self.bridge.state)
