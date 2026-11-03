"""Native setting authority without connecting to Codex."""
import asyncio
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock
from codex_wechat import Bridge
from backend import CodexBackend
from codex_transport import CodexRpcError
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

    async def test_desktop_writer_conflict_keeps_the_bridge_loaded(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root) / "state")
            try:
                bridge = Bridge(store, {"user": "owner"}, {})
                conflict = CodexRpcError(-32600, "thread t already has an active writer")
                bridge.backend.read_thread = AsyncMock(return_value={"status": {"type": "idle"},
                                                                     "name": "name", "cwd": "/cwd"})
                bridge.backend.thread = AsyncMock(side_effect=conflict)
                await activate(bridge, "t")
                self.assertTrue(bridge.loaded)
                self.assertEqual(bridge.state["thread"], "t")
                self.assertEqual(bridge.backend.selected_thread, "t")
                self.assertEqual(bridge.backend.writer_conflict, "t")
                self.assertEqual(bridge.backend.info["cwd"], "/cwd")
            finally:
                store.lock.close()

    async def test_next_turn_takes_the_writer_back_after_a_conflict(self):
        backend = CodexBackend({}, "/tmp", lambda _: None)
        backend.transport.proc = SimpleNamespace(returncode=None)
        backend.transport.reader = SimpleNamespace(done=lambda: False)
        backend.writer_conflict = "t"
        backend.input_items = AsyncMock(return_value=[])
        backend.thread = AsyncMock(side_effect=CodexRpcError(
            -32600, "thread t already has an active writer"))
        with self.assertRaises(CodexRpcError):
            await backend.run("t", "hello")
        self.assertEqual(backend.writer_conflict, "t")

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
