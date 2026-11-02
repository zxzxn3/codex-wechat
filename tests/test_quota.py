"""Threshold detection using synthetic account snapshots only."""
import unittest

from configuration import Notifications
from quota import quota_warnings


class QuotaTests(unittest.TestCase):
    def snapshot(self, remaining, reset=100):
        return {"rateLimits": {"limitId": "codex", "primary": {
            "usedPercent": 100 - remaining, "resetsAt": reset, "windowDurationMins": 300}}}

    def test_baseline_crossings_and_no_duplicates(self):
        records = {}
        def check(remaining):
            return quota_warnings(records, self.snapshot(remaining), "backend", [50, 25, 5], now=10)
        self.assertEqual(check(80), [])
        self.assertEqual(len(check(49)), 1)
        self.assertEqual(check(45), [])
        self.assertIn("阈值 5%", check(4)[0])
        self.assertEqual(check(3), [])
        self.assertEqual(check(30), [])
        self.assertEqual(check(20), [])
        self.assertEqual(check(80), [])
        self.assertEqual(len(check(49)), 1)

    def test_sparse_legacy_updates_override_multibucket_cache(self):
        records = {}
        sample = self.snapshot(80)
        sample["rateLimitsByLimitId"] = {"codex": sample["rateLimits"]}
        quota_warnings(records, sample, "backend", [50])
        sample["rateLimits"] = self.snapshot(49)["rateLimits"]
        self.assertEqual(len(quota_warnings(records, sample, "backend", [50])), 1)

    def test_disabled_invalid_and_backend_isolation(self):
        records = {}
        self.assertEqual(quota_warnings(records, self.snapshot(1), "backend", []), [])
        self.assertFalse(records)
        self.assertEqual(quota_warnings(records, self.snapshot(float("nan")), "backend", [50]), [])
        quota_warnings(records, self.snapshot(80), "a", [50])
        self.assertEqual(quota_warnings(records, self.snapshot(40), "b", [50]), [])
        self.assertEqual(len(quota_warnings(records, self.snapshot(40), "a", [50])), 1)

    def test_configuration_defaults_and_validation(self):
        self.assertFalse(Notifications.from_config({}).compaction)
        self.assertEqual(Notifications.from_config({}).quota_thresholds, (50, 25, 10, 5, 1))
        for config in ({"compactionNotice": "on"}, {"quotaWarningThresholds": [True]},
                       {"quotaWarningThresholds": [-1]}):
            with self.assertRaises(ValueError):
                Notifications.from_config(config)

    def test_default_threshold_includes_ten_percent(self):
        records = {}
        thresholds = Notifications.from_config({}).quota_thresholds
        self.assertEqual(quota_warnings(records, self.snapshot(20), "backend",
                                        thresholds, now=10), [])
        warnings = quota_warnings(records, self.snapshot(9), "backend",
                                  thresholds, now=10)
        self.assertEqual(len(warnings), 1)
        self.assertIn("阈值 10%", warnings[0])

    def test_default_threshold_includes_one_percent(self):
        records = {}
        thresholds = Notifications.from_config({}).quota_thresholds
        self.assertEqual(quota_warnings(records, self.snapshot(2), "backend",
                                        thresholds, now=10), [])
        warnings = quota_warnings(records, self.snapshot(1), "backend",
                                  thresholds, now=10)
        self.assertEqual(len(warnings), 1)
        self.assertIn("阈值 1%", warnings[0])
