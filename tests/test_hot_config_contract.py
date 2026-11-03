"""Hot reload keys must cover every source in the validated hot settings."""
import json
from pathlib import Path
import unittest

from configuration import HOT_CONFIG_KEYS, HotSettings


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
        }
        self.assertEqual(set(HOT_CONFIG_KEYS), set(key_fields))
        self.assertEqual({field for field in key_fields.values() if field},
                         set(HotSettings.__dataclass_fields__))

    def test_example_config_matches_the_hot_settings_contract(self):
        path = Path(__file__).resolve().parents[1] / "config.example.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        settings = HotSettings.from_config(config)
        self.assertEqual(settings.notifications.quota_thresholds, {
            "primary": (50, 25, 10, 5, 1),
            "secondary": (50, 25, 10, 5, 1),
        })
