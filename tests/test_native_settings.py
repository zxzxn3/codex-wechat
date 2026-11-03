"""Native setting authority without connecting to Codex."""
import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock

from codex_wechat import Bridge
from backend import CodexBackend
from commands import activate
from storage import Store


class NativeSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def test_restore_ignores_old_mobile_settings_but_explicit_edit_is_forwarded(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root) / "state")
            try:
                bridge = Bridge(store, {"user": "owner"}, {})
                bridge.state["sessionOptions"] = {"t": {"model": "stale"}}
                bridge.backend.read_thread = AsyncMock(return_value={"status": {"type": "idle"}})
                bridge.backend.thread = AsyncMock(return_value="t")
                await activate(bridge, "t")
                bridge.backend.thread.assert_awaited_once_with("t", {})
                self.assertNotIn("sessionOptions", bridge.state)
                await activate(bridge, "t", {"effort": "high"})
                self.assertEqual(bridge.backend.thread.call_args.args, ("t", {"effort": "high"}))
            finally:
                store.lock.close()

    async def test_activation_returns_the_writer_so_the_app_can_open_the_thread(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root) / "state")
            try:
                bridge = Bridge(store, {"user": "owner"}, {})
                bridge.backend.read_thread = AsyncMock(return_value={"status": {"type": "idle"}})
                bridge.backend.thread = AsyncMock(
                    side_effect=lambda thread_id=None, overrides=None: thread_id or "created")
                bridge.backend.release_thread = AsyncMock(return_value=True)
                await activate(bridge, "target")
                bridge.backend.release_thread.assert_awaited_once_with("target")
                self.assertEqual(bridge.state["thread"], "target")
                bridge.backend.release_thread.reset_mock()
                await activate(bridge, "other")
                self.assertEqual([call.args for call in bridge.backend.release_thread.await_args_list],
                                 [("target",), ("other",)])
            finally:
                store.lock.close()

    async def test_settings_event_updates_only_selected_thread(self):
        backend = CodexBackend({}, "/tmp", lambda _: None)
        backend.selected_thread = "t"
        backend.event({"method": "thread/settings/updated", "params": {"threadId": "other",
                       "threadSettings": {"model": "other"}}})
        self.assertEqual(backend.info, {})
        backend.event({"method": "thread/settings/updated", "params": {"threadId": "t",
                       "threadSettings": {"model": "desktop", "effort": None, "cwd": "/tmp"}}})
        self.assertEqual(backend.info, {"model": "desktop", "effort": None, "cwd": "/tmp"})

    async def test_new_turn_does_not_reapply_cached_settings(self):
        backend = CodexBackend({"sharedDaemon": False}, "/tmp", lambda _: None)
        backend.selected_thread = "t"
        backend.info = {"model": "old", "effort": "high", "cwd": "/old"}
        backend.input_items = AsyncMock(return_value=[])
        async def rpc(method, params):
            if method == "turn/start":
                self.assertEqual(params, {"threadId": "t", "input": [], "clientUserMessageId": "client"})
                backend.event({"method": "turn/completed", "params": {"threadId": "t",
                              "turn": {"id": "turn", "status": "completed"}}})
                return {"turn": {"id": "turn"}}
            return {}
        backend.rpc = rpc
        await asyncio.wait_for(backend.run("t", "input", client_id="client"), 1)
