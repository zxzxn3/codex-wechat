"""Truth tables for the operation-specific lifecycle gates."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from codex_wechat import commands
from codex_wechat.errors import UserFacingError
from codex_wechat.app import Bridge
from codex_wechat.lifecycle import (
    can_change_session,
    can_prepare_input,
    can_restart,
    can_steer_input,
)
from codex_wechat.records import InputPhase
from codex_wechat.storage import Store


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "state")
        self.bridge = Bridge(self.store, {"user": "owner"}, {})
        self.bridge.state.update(context="context", thread="thread")

    async def asyncTearDown(self):
        self.store.lock.close()
        self.temp.cleanup()

    def assertGate(self, predicate, expected, name):
        self.assertEqual(predicate(self.bridge), expected, name)

    async def test_session_changes_require_a_fully_idle_bridge(self):
        self.assertGate(can_change_session, True, "idle")
        for flag in ("restarting", "running", "steering"):
            setattr(self.bridge, flag, True)
            self.assertGate(can_change_session, False, flag)
            setattr(self.bridge, flag, False)
        self.bridge.backend.active = self.bridge.backend.new_turn(
            "thread", "turn", owned=False)
        self.assertGate(can_change_session, False, "active turn")
        self.bridge.backend.active = None
        self.bridge.state["inbox"] = [{"id": "queued", "mode": "queue"}]
        self.assertGate(can_change_session, False, "inbox")
        self.bridge.state["inbox"] = []
        async with self.bridge.session_lock:
            self.assertGate(can_change_session, False, "session lock")
        self.assertGate(can_change_session, True, "released")

    async def test_effort_query_is_available_during_work_but_changes_are_blocked(self):
        self.bridge.running = True
        self.bridge.backend.info = {"model": "fixture", "effort": "high"}
        self.bridge.backend.models = AsyncMock(return_value=[{
            "model": "fixture", "supportedReasoningEfforts": [{"reasoningEffort": "high"}]}])
        self.bridge.reply = replies = Mock()
        with patch.object(commands, "ensure_session", AsyncMock()), \
                patch.object(commands, "_apply_settings", AsyncMock()) as apply:
            await commands.command_effort(self.bridge, "/effort", "")
            replies.assert_called_once()
            apply.assert_not_awaited()
            self.bridge.backend.models.reset_mock()
            with self.assertRaises(UserFacingError):
                await commands.command_effort(self.bridge, "/effort", "high")
            self.bridge.backend.models.assert_not_awaited()
            apply.assert_not_awaited()

    async def test_restart_allows_queued_work_but_not_submitted_work(self):
        self.bridge.state["inbox"] = [{"id": "queued"}]
        self.assertGate(can_restart, True, "queued work does not block restart")
        self.bridge.state["currentInput"] = {"phase": InputPhase.PREPARING}
        self.bridge.running = True
        self.assertGate(can_restart, True, "preparing work is recoverable")
        self.bridge.state["currentInput"] = {"phase": InputPhase.ACCEPTED}
        self.assertGate(can_restart, False, "accepted work is not replayed")
        self.bridge.state.pop("currentInput")
        self.assertGate(can_restart, False, "running without current input")
        self.bridge.running = False
        async with self.bridge.session_lock:
            self.assertGate(can_restart, False, "session lock")
            self.bridge.backend_recovering = True
            self.assertGate(can_restart, True, "recovery owns the lock")

    async def test_input_preparation_is_independent_of_authentication(self):
        self.assertGate(can_prepare_input, True, "idle")
        self.bridge.wechat_paused = True
        self.assertGate(can_prepare_input, True, "auth has its own wake path")
        self.bridge.wechat_paused = False
        for flag in ("restarting", "steering", "input_paused"):
            setattr(self.bridge, flag, True)
            self.assertGate(can_prepare_input, False, flag)
            setattr(self.bridge, flag, False)
        self.bridge.state["wechatContextPending"] = True
        self.assertGate(can_prepare_input, False, "pending context")
        self.bridge.state.pop("wechatContextPending")
        async with self.bridge.session_lock:
            self.assertGate(can_prepare_input, False, "session lock")

    async def test_steering_requires_an_active_turn_and_waits_for_protocol_state(self):
        self.assertGate(can_steer_input, False, "idle")
        self.bridge.running = True
        self.assertGate(can_steer_input, False, "no inbox")
        self.bridge.state["inbox"] = [{"id": "input", "mode": "steer"}]
        self.assertGate(can_steer_input, True, "running with input")
        for flag in ("stopping", "restarting", "wechat_paused"):
            setattr(self.bridge, flag, True)
            self.assertGate(can_steer_input, False, flag)
            setattr(self.bridge, flag, False)
        self.bridge.state["wechatContextPending"] = True
        self.assertGate(can_steer_input, False, "pending context")
        self.bridge.state.pop("wechatContextPending")
        self.bridge.running = False
        self.bridge.backend.active = self.bridge.backend.new_turn(
            "thread", "turn", owned=False)
        self.assertGate(can_steer_input, True, "active native turn")


if __name__ == "__main__":
    unittest.main()
