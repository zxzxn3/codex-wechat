"""Aliases follow the canonical route and cannot abbreviate protected actions."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from codex_wechat import Bridge
from command_specs import REGISTRY, command_lane, HELP
from commands import dispatch
from storage import Store


class ShortCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "state")
        self.bridge = Bridge(self.store, {"user": "owner"}, {})
        self.bridge.state["context"] = "context"

    async def asyncTearDown(self):
        self.store.lock.close()
        self.temp.cleanup()

    async def test_aliases_and_protected_commands_share_one_registry(self):
        for short, full in (("/s", "/status"), ("/q", "/queue"), ("/m", "/model"),
                            ("/e", "/effort"), ("/p", "/ping"), ("/h", "/help")):
            self.assertIs(REGISTRY[short], REGISTRY[full])
            self.assertEqual(command_lane(short, ""), command_lane(full, ""))
            self.assertIn(f"`{short}`", HELP)
        for name in ("/settings", "/sessions", "/switch", "/new", "/stop", "/restart",
                     "/backend", "/cwd", "/approve", "/deny", "/send", "/answer", "/compact"):
            self.assertFalse(REGISTRY[name].aliases)

    async def test_status_alias_is_local_and_rejects_extra_args(self):
        self.bridge.ensure_backend = AsyncMock()
        await dispatch(self.bridge, "/s")
        self.assertIn("状态", self.bridge.state["outbox"][-1]["text"])
        await dispatch(self.bridge, "/s invalid")
        self.assertIn("用法", self.bridge.state["outbox"][-1]["text"])
        self.bridge.ensure_backend.assert_not_called()

    async def test_model_alias_passes_arguments_to_canonical_handler(self):
        handler = AsyncMock()
        self.bridge.ensure_backend = AsyncMock()
        with patch.dict("commands.HANDLERS", {"/model": handler}):
            await dispatch(self.bridge, "/m 2")
        handler.assert_awaited_once_with(self.bridge, "/model", "2", None)
        self.bridge.ensure_backend.assert_awaited_once()

    async def test_queue_alias_strips_prefix_and_preserves_literal_command(self):
        await self.bridge.receive({"msgs": [{"message_id": "q", "from_user_id": "owner",
            "message_type": 1, "context_token": "context", "item_list": [
                {"type": 1, "text_item": {"text": "/q /stop is literal"}}]}]})
        jobs = self.store.load("bridge")["inbox"]
        self.assertEqual([(job["text"], job["mode"]) for job in jobs], [("/stop is literal", "queue")])
        self.assertEqual(self.bridge.state["outbox"][-1]["text"], "Queue 1")
        self.assertTrue(all(lane.empty() for lane in self.bridge.controls.values()))
