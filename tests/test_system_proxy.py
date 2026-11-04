"""A proxied machine must name its proxy to the backend, or every first turn stalls."""
import os
import unittest
from unittest.mock import patch

from codex_connection import ConnectionSpec
from platform_support import apply_system_proxy

PROXY = {"HTTPS_PROXY": "http://127.0.0.1:7897", "HTTP_PROXY": "http://127.0.0.1:7897"}


class SystemProxyTests(unittest.TestCase):
    def test_detected_proxy_fills_both_schemes_and_keeps_localhost_direct(self):
        env = {}
        with patch("platform_support.system_proxy", return_value=dict(PROXY)):
            apply_system_proxy(env)
        self.assertEqual(env["HTTPS_PROXY"], "http://127.0.0.1:7897")
        self.assertEqual(env["HTTP_PROXY"], "http://127.0.0.1:7897")
        self.assertIn("127.0.0.1", env["NO_PROXY"])

    def test_an_environment_that_already_names_a_proxy_is_untouched(self):
        env = {"HTTPS_PROXY": "http://localhost:1234"}
        with patch("platform_support.system_proxy", return_value=dict(PROXY)) as probe:
            apply_system_proxy(env)
        probe.assert_not_called()
        self.assertEqual(env, {"HTTPS_PROXY": "http://localhost:1234"})

    def test_no_detected_proxy_leaves_the_environment_alone(self):
        env = {"PATH": "x"}
        with patch("platform_support.system_proxy", return_value={}):
            apply_system_proxy(env)
        self.assertEqual(env, {"PATH": "x"})

    def test_all_proxy_also_counts_as_an_explicit_choice(self):
        env = {"ALL_PROXY": "socks5://localhost:1080"}
        with patch("platform_support.system_proxy", return_value=dict(PROXY)) as probe:
            apply_system_proxy(env)
        probe.assert_not_called()
        self.assertEqual(env, {"ALL_PROXY": "socks5://localhost:1080"})

    def test_backend_environment_carries_the_system_proxy(self):
        spec = ConnectionSpec.from_config({"command": ["codex", "app-server"]})
        with patch("platform_support.system_proxy", return_value=dict(PROXY)), \
                patch.dict(os.environ, {}, clear=False):
            env = spec.environment({})
        self.assertEqual(env["HTTPS_PROXY"], "http://127.0.0.1:7897")

    def test_backend_config_proxy_wins_over_the_system_proxy(self):
        spec = ConnectionSpec.from_config({"command": ["codex", "app-server"]})
        with patch("platform_support.system_proxy", return_value=dict(PROXY)):
            env = spec.environment({"env": {"HTTPS_PROXY": "http://user:1080"}})
        self.assertEqual(env["HTTPS_PROXY"], "http://user:1080")


if __name__ == "__main__":
    unittest.main()
