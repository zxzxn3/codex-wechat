"""Hot reload keys must cover every source in the validated hot settings."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from codex_wechat.configuration import (
    ConfigLoader, DEFAULT_COMPLETION_TEMPLATE, HOT_CONFIG_KEYS, HotSettings,
    completion_template, processing_template,
)


class HotConfigContractTests(unittest.TestCase):
    def test_hot_config_keys_cover_every_hot_setting_source(self):
        key_fields = {
            "processingNotice": None,
            "completionNotice": None,
            "sourceHint": "source_hint",
            "slicing": "slicing",
            "compactionNotice": "notifications",
            "quotaWarningThresholds": "notifications",
            "prompts": "prompts",
            "connectionIdleSeconds": "connection_idle_seconds",
            "sending": "sending",
            "logs": "logs",
        }
        self.assertEqual(set(HOT_CONFIG_KEYS), set(key_fields))
        self.assertEqual({field for field in key_fields.values() if field},
                         set(HotSettings.__dataclass_fields__))

    def test_example_config_matches_the_hot_settings_contract(self):
        path = Path(__file__).resolve().parents[2] / "config.example.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        settings = HotSettings.from_config(config)
        self.assertTrue(settings.notifications.compaction)
        self.assertEqual(settings.notifications.quota_thresholds, {
            "primary": (50, 25, 10, 5, 1),
            "secondary": (50, 25, 10, 5, 1),
        })


class HotConfigReloadTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch("codex_wechat.configuration.log_event"))
        self.ticks = 0

    def rewrite(self, path, payload):
        path.write_text(json.dumps(payload))
        # Change detection is (inode, mtime, size); Windows clock ticks can
        # repeat, so equal-length rewrites need an explicit distinct stamp.
        self.ticks += 1
        stamp = 1_700_000_000_000_000_000 + self.ticks * 1_000_000
        os.utime(path, ns=(stamp, stamp))

    def test_group_validation_retention_deletion_and_override_scope(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "config.json"
            initial = {
                "processingNotice": "file ack",
                "completionNotice": "file done",
                "sourceHint": "hint-a",
                "slicing": {"smart": True, "targetChars": 600},
                "compactionNotice": False,
                "quotaWarningThresholds": {"primary": [50], "secondary": [25]},
            }
            self.rewrite(path, initial)
            config = dict(initial)
            state = {"processingNotice": "chat ack", "completionNotice": "chat done"}
            loader = ConfigLoader(config, path)

            unrelated = dict(initial, sourceHint="hint-b",
                             slicing={"smart": False, "targetChars": 800})
            self.rewrite(path, unrelated)
            self.assertFalse(loader.reload(state))
            self.assertEqual(config["sourceHint"], "hint-b")
            self.assertEqual(loader.hot.slicing.target_chars, 800)
            self.assertEqual(state["processingNotice"], "chat ack")

            invalid = dict(unrelated, sourceHint="must-not-apply",
                           slicing={"smart": False, "targetChars": 1},
                           compactionNotice=True)
            self.rewrite(path, invalid)
            self.assertFalse(loader.reload(state))
            self.assertEqual(config["sourceHint"], "hint-b")
            self.assertEqual(loader.hot.slicing.target_chars, 800)
            self.assertFalse(loader.hot.notifications.compaction)
            self.assertEqual(state["completionNotice"], "chat done")

            deleted = dict(unrelated, sourceHint="hint-c",
                           slicing={"smart": False, "targetChars": 900})
            deleted.pop("processingNotice")
            deleted.pop("completionNotice")
            self.rewrite(path, deleted)
            self.assertTrue(loader.reload(state))
            self.assertNotIn("processingNotice", config)
            self.assertNotIn("completionNotice", config)
            self.assertNotIn("processingNotice", state)
            self.assertNotIn("completionNotice", state)
            self.assertEqual(processing_template(config, state),
                             "`IN < ~{turn_total_tokens} ({ingress_ms}ms)`")
            self.assertEqual(completion_template(config, state),
                             DEFAULT_COMPLETION_TEMPLATE)

            state["processingNotice"] = "new chat override"
            unrelated_hot = dict(deleted, quotaWarningThresholds={
                "primary": [25], "secondary": [10]})
            self.rewrite(path, unrelated_hot)
            self.assertFalse(loader.reload(state))
            self.assertEqual(state["processingNotice"], "new chat override")
            self.assertEqual(loader.hot.notifications.quota_thresholds,
                             {"primary": (25,), "secondary": (10,)})
