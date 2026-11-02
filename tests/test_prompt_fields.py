"""Template contracts, using temporary state and no external operations."""
import tempfile
import unittest
import re
from unittest.mock import AsyncMock

from codex_wechat import Bridge
from commands import handle
from prompts import prompt_text, render_ack, status_from_fields
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
                self.assertTrue(all(re.fullmatch(r'[a-z][a-z0-9_]*', name)
                                    for name in fields))
                canonical_template = ('{session} {model} ({effort}) {ingress_ms}/{egress_ms}ms '
                                      '{rate_limits} {unknown}')
                self.assertEqual(render_ack(canonical_template, fields),
                                 '{模型} example (High) 120/80ms - {unknown}')
                self.assertIn("模型: example (High)", status_from_fields(fields))
                bridge.state["processingNotice"] = "{model} {provider_latency_ms}ms"
                bridge.latency.provider_sample = (bridge.backend.connection_id, "185ms")
                bridge.acknowledge({})
                self.assertEqual(bridge.state["outbox"][-1]["text"], "example 185ms")
                bridge.latency.provider_sample = ("old-connection", "999ms")
                self.assertEqual(bridge.prompt_fields()["provider_latency_ms"], "-")
                await handle(bridge, "/ack", "fields")
                self.assertIn("{context_remaining}", bridge.state["outbox"][-1]["text"])
                self.assertNotIn("{上下文剩余}", bridge.state["outbox"][-1]["text"])
                bridge.backend.rpc.assert_not_awaited()
            finally:
                store.lock.close()

    def test_old_chinese_fields_are_not_translated(self):
        self.assertEqual(render_ack('{input_tokens}/{输入Token}', {'input_tokens': 'new'}),
                         'new/{输入Token}')
        self.assertEqual(prompt_text('QUEUE_CONFIRMED', {'count': 2, 'unit': 'tasks'},
                                    overrides={'QUEUE_CONFIRMED': '{数量} {单位}'}),
                         '{数量} {单位}')

    def test_local_snake_case_arguments_are_rendered_once(self):
        self.assertEqual(prompt_text('QUEUE_CONFIRMED', {'count': 2, 'unit': '{model}'}),
                         'Queue 2 {model}')
