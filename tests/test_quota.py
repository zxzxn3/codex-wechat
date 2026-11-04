"""Threshold detection using synthetic account snapshots only."""
import unittest

from configuration import Notifications
from quota import quota_warnings


class QuotaTests(unittest.TestCase):
    def snapshot(self, remaining, reset=100):
        return {"rateLimits": {"limitId": "codex", "primary": {
            "usedPercent": 100 - remaining, "resetsAt": reset, "windowDurationMins": 300}}}

    def dual_snapshot(self, primary, secondary, reset=100):
        return {"rateLimits": {"limitId": "codex",
            "primary": {"usedPercent": 100 - primary, "resetsAt": reset,
                        "windowDurationMins": 300},
            "secondary": {"usedPercent": 100 - secondary, "resetsAt": reset,
                          "windowDurationMins": 10080}}}

    def limits(self, primary=(50, 25, 5), secondary=(50, 25, 5)):
        return {"primary": tuple(primary), "secondary": tuple(secondary)}

    def test_baseline_crossings_and_no_duplicates(self):
        records = {}
        def check(remaining):
            return quota_warnings(records, self.snapshot(remaining), "backend",
                                  self.limits(), now=10)
        self.assertEqual(check(80), [])
        self.assertEqual(len(check(49)), 1)
        self.assertEqual(check(45), [])
        warning = check(4)[0]
        self.assertIn("剩余 4%", warning)
        self.assertNotIn("阈值", warning)
        self.assertEqual(check(3), [])
        self.assertEqual(check(30), [])
        self.assertEqual(check(20), [])
        self.assertEqual(check(80), [])
        self.assertEqual(len(check(49)), 1)

    def test_sparse_legacy_updates_override_multibucket_cache(self):
        records = {}
        sample = self.snapshot(80)
        sample["rateLimitsByLimitId"] = {"codex": sample["rateLimits"]}
        thresholds = self.limits((50,))
        quota_warnings(records, sample, "backend", thresholds)
        sample["rateLimits"] = self.snapshot(49)["rateLimits"]
        self.assertEqual(len(quota_warnings(records, sample, "backend", thresholds)), 1)

    def test_disabled_invalid_and_backend_isolation(self):
        records = {}
        self.assertEqual(quota_warnings(
            records, self.snapshot(1), "backend", self.limits((), ())), [])
        self.assertFalse(records)
        thresholds = self.limits((50,))
        self.assertEqual(quota_warnings(
            records, self.snapshot(float("nan")), "backend", thresholds), [])
        quota_warnings(records, self.snapshot(80), "a", thresholds)
        self.assertEqual(quota_warnings(records, self.snapshot(40), "b", thresholds), [])
        self.assertEqual(len(quota_warnings(records, self.snapshot(40), "a", thresholds)), 1)

    def test_primary_and_secondary_use_independent_thresholds(self):
        records = {}
        thresholds = self.limits((50, 25, 5), (10, 1))
        self.assertEqual(quota_warnings(
            records, self.dual_snapshot(80, 80), "backend", thresholds, now=10), [])

        primary = quota_warnings(
            records, self.dual_snapshot(49, 80), "backend", thresholds, now=10)
        self.assertEqual(len(primary), 1)
        self.assertIn("5小时剩余 49%", primary[0])

        self.assertEqual(quota_warnings(
            records, self.dual_snapshot(49, 70), "backend", thresholds, now=10), [])
        secondary = quota_warnings(
            records, self.dual_snapshot(49, 9), "backend", thresholds, now=10)
        self.assertEqual(len(secondary), 1)
        self.assertIn("7天剩余 9%", secondary[0])

    def test_malformed_cached_state_starts_a_safe_baseline(self):
        thresholds = self.limits((50,))
        for old in ([], "bad", {}, {"reset": 100, "warned": []},
                    {"remaining": None, "reset": 100, "warned": []},
                    {"remaining": "80", "reset": 100, "warned": []}):
            with self.subTest(old=old):
                records = {}
                quota_warnings(records, self.snapshot(80), "backend", thresholds, now=10)
                records[next(iter(records))] = old
                self.assertEqual(quota_warnings(
                    records, self.snapshot(49), "backend", thresholds, now=10), [])

    def test_malformed_cached_fields_do_not_break_normal_crossings(self):
        records = {}
        thresholds = self.limits((50,))
        quota_warnings(records, self.snapshot(80), "backend", thresholds, now=10)
        records[next(iter(records))] = {
            "remaining": 80, "reset": "bad", "warned": "not-a-list"}
        self.assertEqual(len(quota_warnings(
            records, self.snapshot(49), "backend", thresholds, now=10)), 1)
        self.assertEqual(quota_warnings(
            records, self.snapshot(40), "backend", thresholds, now=10), [])

    def test_configuration_defaults_and_validation(self):
        self.assertTrue(Notifications.from_config({}).compaction)
        self.assertEqual(Notifications.from_config({}).quota_thresholds, {
            "primary": (50, 25, 10, 5, 1),
            "secondary": (50, 25, 10, 5, 1),
        })
        for config in ({"compactionNotice": "on"},
                       {"quotaWarningThresholds": [25]},
                       {"quotaWarningThresholds": {"primary": [True], "secondary": []}},
                       {"quotaWarningThresholds": {"primary": [-1], "secondary": []}},
                       {"quotaWarningThresholds": {"primary": []}},
                       {"quotaWarningThresholds": {
                           "primary": [], "secondary": [], "tertiary": []}}):
            with self.assertRaises(ValueError):
                Notifications.from_config(config)
        self.assertEqual(Notifications.from_config({"quotaWarningThresholds": {
            "primary": [], "secondary": []}}).quota_thresholds,
            {"primary": (), "secondary": ()})

    def test_default_threshold_includes_ten_percent(self):
        records = {}
        thresholds = Notifications.from_config({}).quota_thresholds
        self.assertEqual(quota_warnings(records, self.snapshot(20), "backend",
                                        thresholds, now=10), [])
        warnings = quota_warnings(records, self.snapshot(9), "backend",
                                  thresholds, now=10)
        self.assertEqual(len(warnings), 1)
        self.assertIn("剩余 9%", warnings[0])
        self.assertNotIn("阈值", warnings[0])

    def test_default_threshold_includes_one_percent(self):
        records = {}
        thresholds = Notifications.from_config({}).quota_thresholds
        self.assertEqual(quota_warnings(records, self.snapshot(2), "backend",
                                        thresholds, now=10), [])
        warnings = quota_warnings(records, self.snapshot(1), "backend",
                                  thresholds, now=10)
        self.assertEqual(len(warnings), 1)
        self.assertIn("剩余 1%", warnings[0])
