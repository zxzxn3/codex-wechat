import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from codex_wechat.usage import UsageCache, stored_usage


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


class UsageCacheBoundsTests(unittest.TestCase):
    def test_cache_reads_only_changed_files(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "thread.jsonl"
            path.write_text("first")
            cache = UsageCache()
            with patch("codex_wechat.usage.stored_usage",
                       side_effect=[{"total": 1}, {"total": 2}, None]) as read:
                self.assertEqual(cache.read(path, "thread"), {"total": 1})
                self.assertEqual(cache.read(path, "thread"), {"total": 1})
                self.assertEqual(read.call_count, 1)
                path.write_text("second revision")
                self.assertEqual(cache.read(path, "thread"), {"total": 2})
                cache.clear()
                self.assertIsNone(cache.read(path, "thread"))
                path.unlink()
                self.assertIsNone(cache.read(path, "thread"))

    def test_cache_is_bounded_and_tracks_replacements(self):
        with tempfile.TemporaryDirectory() as root:
            paths = []
            for index in range(25):
                path = Path(root) / f"thread-{index}.jsonl"
                path.write_text(f"revision-{index}")
                paths.append(path)
            cache = UsageCache()
            with patch("codex_wechat.usage.stored_usage",
                       side_effect=lambda *args: {"file": args[0].name}):
                for path in paths:
                    cache.read(path, path.stem)
                self.assertLessEqual(len(cache.entries), 20)
                target = paths[0]
                before = len(cache.entries)
                cache.read(target, target.stem)
                self.assertGreaterEqual(len(cache.entries), before)
                replacement = Path(root) / "replacement"
                replacement.write_text("replacement")
                replacement.replace(target)
                cache.read(target, target.stem)
            cache.clear()
            self.assertEqual(cache.entries, {})
