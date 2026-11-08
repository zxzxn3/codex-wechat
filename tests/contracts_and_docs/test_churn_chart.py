"""The README activity chart stays deterministic and uses the documented metrics."""
from datetime import datetime, timedelta, timezone
import unittest

from scripts.churn_chart import Commit, bucket_series, estimate_hours, render_svg


SHANGHAI = timezone(timedelta(hours=8))


def commit(hour: int, added: int, deleted: int) -> Commit:
    return Commit(
        datetime(2026, 11, 1, 0, tzinfo=SHANGHAI) + timedelta(hours=hour),
        added,
        deleted,
    )


class ChurnChartTests(unittest.TestCase):
    def test_estimate_splits_after_two_idle_hours(self):
        commits = [commit(4, 3, 0), commit(0, 1, 0), commit(5, 4, 0), commit(1, 2, 0)]
        self.assertEqual(estimate_hours(commits), 2.0)

    def test_bucket_series_keeps_empty_buckets(self):
        start, end, buckets = bucket_series([commit(0, 10, 4), commit(36, 8, 2)])
        self.assertEqual((end - start) / timedelta(hours=12), 16)
        self.assertEqual(buckets[13], (10, 4))
        self.assertEqual(buckets[16], (8, 2))
        self.assertEqual(buckets[14], (0, 0))

    def test_render_includes_metrics_and_svg_dimensions(self):
        svg = render_svg([commit(0, 10, 4), commit(1, 20, 8)], 14843, 16573)
        self.assertIn('width="1440" height="360"', svg)
        self.assertIn("14.8K source · 16.6K tests · ~1h dev · 2 commits", svg)
        self.assertIn('fill="#3fb950"', svg)
        self.assertIn('fill="#f85149"', svg)
