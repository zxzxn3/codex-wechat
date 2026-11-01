"""Template contracts, using temporary state and no external operations."""
import tempfile
import unittest
from unittest.mock import AsyncMock

from codex_wechat import Bridge
from commands import handle
from prompts import render_ack, status_from_fields
from storage import Store


class PromptFieldsTests(unittest.IsolatedAsyncioTestCase):
    async def test_shared_snapshot_and_no_recursive_expansion(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(root)
            try:
                bridge = Bridge(store, {"user": "owner"}, {})
                bridge.state["context"] = "ctx"
                bridge.backend.info = {"model": "example", "effort": "high", "name": "{模型}"}
                bridge.backend.rpc = AsyncMock(side_effect=AssertionError("snapshot must be local"))
                bridge.latency.sends.append(80)
                fields = bridge.prompt_fields(120)
                template = "{会话} {模型} ({强度}) {发送延迟}/{接收延迟}ms {限额} {未知}"
                self.assertEqual(render_ack(template, fields),
                                 "{模型} example (High) 120/80ms - {未知}")
                self.assertIn("模型: example (High)", status_from_fields(fields))
                bridge.state["processingNotice"] = "{模型} {供应商延迟}ms"
                bridge.latency.provider_sample = (bridge.backend.connection_id, "185ms")
                bridge.acknowledge({})
                self.assertEqual(bridge.state["outbox"][-1]["text"], "example 185ms")
                bridge.latency.provider_sample = ("old-connection", "999ms")
                self.assertEqual(bridge.prompt_fields()["供应商延迟"], "-")
                await handle(bridge, "/ack", "fields")
                self.assertIn("{上下文剩余}", bridge.state["outbox"][-1]["text"])
                bridge.backend.rpc.assert_not_awaited()
            finally:
                store.lock.close()
