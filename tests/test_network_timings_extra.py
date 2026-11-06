"""Independent checks for real curl failure milestones."""
import json
import unittest

from codex_wechat.latency import curl_timings


class NetworkTimingFailureTests(unittest.TestCase):
    def test_unreachable_connection_does_not_derive_later_phases(self):
        raw = {
            "time_namelookup": 0.000019,
            "time_connect": 0.0,
            "time_appconnect": 0.0,
            "time_pretransfer": 0.000089,
            "time_starttransfer": 0.000089,
            "time_total": 0.000091,
        }
        result = curl_timings(json.dumps(raw).encode())
        self.assertEqual(result["curl_total_ms"], 0.091)
        self.assertNotIn("tls_ms", result)
        self.assertNotIn("response_wait_ms", result)
        self.assertNotIn("download_ms", result)


if __name__ == "__main__":
    unittest.main()
