"""A proxied machine must name its proxy to the backend, or every first turn stalls."""
import os
import unittest
from unittest.mock import patch

from codex_connection import ConnectionSpec
from platform_support import apply_system_proxy, local_endpoint, proxy_label

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

    def test_inheriting_the_system_proxy_is_recorded(self):
        spec = ConnectionSpec.from_config({"command": ["codex", "app-server"]})
        with patch("platform_support.system_proxy", return_value=dict(PROXY)), \
                patch("codex_connection.log_event") as log:
            spec.environment({})
        log.assert_called_once_with("backend.proxy_inherited", component="backend",
                                    source="system", proxy="127.0.0.1:7897")

    def test_an_explicit_proxy_records_nothing(self):
        spec = ConnectionSpec.from_config({"command": ["codex", "app-server"]})
        with patch("codex_connection.log_event") as log:
            spec.environment({"env": {"HTTPS_PROXY": "http://user:1080"}})
        log.assert_not_called()

    def test_proxy_label_never_carries_credentials(self):
        self.assertEqual(proxy_label("http://user:secret@10.0.0.5:8080"), "10.0.0.5:8080")
        self.assertEqual(proxy_label("http://127.0.0.1:7897"), "127.0.0.1:7897")
        self.assertEqual(proxy_label("not a url"), "")

    def test_nothing_is_returned_when_the_proxy_was_already_named(self):
        self.assertEqual(apply_system_proxy({"HTTPS_PROXY": "http://x:1"}), {})

    def test_a_local_model_gateway_is_left_direct(self):
        for base in ("http://127.0.0.1:1234/v1", "http://localhost:11434/v1",
                     "http://192.168.1.9:8000", "http://10.0.0.4:8080",
                     "http://gateway:8080", "http://lmstudio.local/v1"):
            with self.subTest(base=base), \
                    patch("platform_support.system_proxy", return_value=dict(PROXY)) as probe:
                env = {"OPENAI_BASE_URL": base}
                self.assertEqual(apply_system_proxy(env), {})
                self.assertEqual(env, {"OPENAI_BASE_URL": base})
                probe.assert_not_called()

    def test_a_remote_base_url_still_gets_the_proxy(self):
        env = {"OPENAI_BASE_URL": "https://api.example.com/v1"}
        with patch("platform_support.system_proxy", return_value=dict(PROXY)):
            self.assertTrue(apply_system_proxy(env))
        self.assertEqual(env["HTTPS_PROXY"], "http://127.0.0.1:7897")

    def test_local_endpoint_covers_loopback_and_intranet_names(self):
        self.assertTrue(local_endpoint("http://127.0.0.1:1"))
        self.assertTrue(local_endpoint("http://[::1]:1"))
        self.assertTrue(local_endpoint("http://172.16.3.4"))
        self.assertFalse(local_endpoint("https://chatgpt.com/backend-api/codex"))
        self.assertFalse(local_endpoint(""))
        self.assertFalse(local_endpoint("not a url"))


if __name__ == "__main__":
    unittest.main()
