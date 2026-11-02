import json
from pathlib import Path
import tempfile
import unittest

from usage import stored_usage


class StoredUsageTests(unittest.TestCase):
    def test_latest_metadata_and_partial_tail(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "rollout-thread.jsonl"
            record = {"type": "event_msg", "payload": {"type": "token_count", "info": {
                "total_token_usage": {"total_tokens": 1234, "input_tokens": 1000},
                "last_token_usage": {"total_tokens": 100}, "model_context_window": 200000}}}
            path.write_text(json.dumps(record) + '\n{"type":"event_msg","payload":{"type":"token_count"')
            usage = stored_usage(path, "thread")
            self.assertEqual(usage["total"]["totalTokens"], 1234)
            self.assertEqual(usage["last"]["totalTokens"], 100)
            self.assertEqual(usage["modelContextWindow"], 200000)
            self.assertIsNone(stored_usage(path, "unrelated"))

    def test_message_text_is_not_a_usage_record(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "rollout-thread.jsonl"
            path.write_text(json.dumps({"type": "response_item", "payload": {
                "type": "token_count", "info": {"total_token_usage": {"total_tokens": 999}}}}))
            self.assertIsNone(stored_usage(path, "thread"))
