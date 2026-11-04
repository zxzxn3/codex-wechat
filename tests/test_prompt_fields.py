"""Template contracts, using temporary state and no external operations."""
import tempfile
import unittest
import re
from unittest.mock import AsyncMock

from codex_wechat import Bridge
from commands import handle
from prompts import (VALUE_UNAVAILABLE, context_usage, duration_label, prompt_text,
                     rate_limit_label, render_ack, status_fields, status_from_fields,
                     token_count)
from storage import Store


class PromptFieldsTests(unittest.IsolatedAsyncioTestCase):
    def test_turn_token_fields_use_last_usage(self):
        values = status_fields(
            status="空闲", queued=0, pending=0, failed=0, name="session",
            model="model", effort="high", cwd="/tmp", connection="独立进程",
            usage={
                "total": {"totalTokens": 1234, "inputTokens": 1000, "outputTokens": 234},
                "last": {"totalTokens": 100, "inputTokens": 80, "outputTokens": 20},
                "modelContextWindow": 200000,
            })
        self.assertEqual(values["total_tokens"], "1.2K")
        self.assertEqual(values["input_tokens"], "1K")
        self.assertEqual(values["output_tokens"], "234")
        self.assertEqual(values["turn_total_tokens"], "100")
        self.assertEqual(values["turn_input_tokens"], "80")
        self.assertEqual(values["turn_output_tokens"], "20")

    async def test_turn_duration_label(self):
        self.assertEqual(duration_label(None), "-")
        self.assertEqual(duration_label(9.66), "9.7s")
        self.assertEqual(duration_label(65.4), "1m05s")
        self.assertEqual(duration_label(186.6), "3m07s")

    async def test_elapsed_field_is_available_to_templates(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(root)
            try:
                bridge = Bridge(store, {"user": "owner"}, {})
                self.assertEqual(bridge.prompt_fields()["elapsed"], "-")
                bridge.last_turn_seconds = 65.4
                self.assertEqual(bridge.prompt_fields()["elapsed"], "1m05s")
            finally:
                store.close()

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
                                 '{模型} example (High) 120/80ms N/A {unknown}')
                self.assertIn("模型: example (High)", status_from_fields(fields))
                bridge.state["processingNotice"] = "{model} {provider_latency_ms}ms"
                bridge.latency.provider_sample = (bridge.backend.connection_id, "185ms")
                bridge.acknowledge({})
                self.assertEqual(bridge.state["outbox"][-1]["text"], "example 185ms")
                bridge.latency.provider_sample = ("old-connection", "999ms")
                self.assertEqual(bridge.prompt_fields()["provider_latency_ms"], "-")
                await handle(bridge, "/settings", "ack fields")
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
                         '已排队: 2 {model}')

    def test_unavailable_values_use_short_english_placeholder(self):
        self.assertEqual(VALUE_UNAVAILABLE, "N/A")
        self.assertEqual(token_count(None), "N/A")
        self.assertEqual(context_usage(None), "N/A")
        self.assertEqual(rate_limit_label(None), "N/A")
        self.assertEqual(render_ack("{value}", {"value": None}), "N/A")
        values = status_fields(
            status="空闲", queued=0, pending=0, failed=0, name="session",
            model="model", effort="high", cwd="/tmp", connection="独立进程")
        for field in ("permissions", "context_usage", "context_remaining",
                      "total_tokens", "turn_total_tokens", "rate_limits"):
            self.assertEqual(values[field], "N/A")
