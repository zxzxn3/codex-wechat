"""A proxied machine must name its proxy to the backend, or every first turn stalls."""
import os
import unittest
from unittest.mock import patch

from codex_wechat.codex_connection import ConnectionSpec
from codex_wechat.platform_support import (
    apply_system_proxy, endpoint_host, local_endpoint, proxy_label, system_proxy,
)

PROXY = {"HTTPS_PROXY": "http://127.0.0.1:7897", "HTTP_PROXY": "http://127.0.0.1:7897"}
# Fixtures must not inherit whatever proxy the machine or CI runner carries.
AMBIENT = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy",
           "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy")


def no_ambient_proxy():
    return patch.dict(os.environ, {k: v for k, v in os.environ.items() if k not in AMBIENT},
                      clear=True)


class SystemProxyTests(unittest.TestCase):
    def test_linux_gnome_manual_proxy_is_used_when_environment_is_empty(self):
        settings = {
            ("org.gnome.system.proxy", "mode"): "manual",
            ("org.gnome.system.proxy", "use-same-proxy"): True,
            ("org.gnome.system.proxy.http", "host"): "127.0.0.1",
            ("org.gnome.system.proxy.http", "port"): 7897,
            ("org.gnome.system.proxy.https", "host"): "127.0.0.1",
            ("org.gnome.system.proxy.https", "port"): 7897,
            ("org.gnome.system.proxy.socks", "host"): "127.0.0.1",
            ("org.gnome.system.proxy.socks", "port"): 7897,
            ("org.gnome.system.proxy", "ignore-hosts"): ["localhost", "127.0.0.1"],
        }
        with patch("codex_wechat.platform_support.getproxies", return_value={}), \
                patch("codex_wechat.platform_support.sys.platform", "linux"), \
                patch("codex_wechat.platform_support._gsettings", side_effect=lambda schema, key: settings[(schema, key)]):
            self.assertEqual(system_proxy(), {
                "HTTP_PROXY": "http://127.0.0.1:7897",
                "HTTPS_PROXY": "http://127.0.0.1:7897",
                "NO_PROXY": "localhost,127.0.0.1",
            })

    def test_gnome_automatic_mode_is_not_guessed(self):
        with patch("codex_wechat.platform_support.getproxies", return_value={}), \
                patch("codex_wechat.platform_support.sys.platform", "linux"), \
                patch("codex_wechat.platform_support._gsettings", return_value="auto"):
            self.assertEqual(system_proxy(), {})

    def test_gnome_keeps_http_and_https_proxies_separate(self):
        settings = {
            ("org.gnome.system.proxy", "mode"): "manual",
            ("org.gnome.system.proxy", "use-same-proxy"): False,
            ("org.gnome.system.proxy.http", "host"): "127.0.0.1",
            ("org.gnome.system.proxy.http", "port"): 7890,
            ("org.gnome.system.proxy.https", "host"): "127.0.0.1",
            ("org.gnome.system.proxy.https", "port"): 7891,
            ("org.gnome.system.proxy.socks", "host"): "",
            ("org.gnome.system.proxy.socks", "port"): 0,
            ("org.gnome.system.proxy", "ignore-hosts"): [],
        }
        with patch("codex_wechat.platform_support.getproxies", return_value={}), \
                patch("codex_wechat.platform_support.sys.platform", "linux"), \
                patch("codex_wechat.platform_support._gsettings",
                      side_effect=lambda schema, key: settings[(schema, key)]):
            self.assertEqual(system_proxy(), {
                "HTTP_PROXY": "http://127.0.0.1:7890",
                "HTTPS_PROXY": "http://127.0.0.1:7891",
            })

    def test_gnome_socks_only_is_exposed_as_all_proxy(self):
        settings = {
            ("org.gnome.system.proxy", "mode"): "manual",
            ("org.gnome.system.proxy", "use-same-proxy"): False,
            ("org.gnome.system.proxy.http", "host"): "",
            ("org.gnome.system.proxy.http", "port"): 0,
            ("org.gnome.system.proxy.https", "host"): "",
            ("org.gnome.system.proxy.https", "port"): 0,
            ("org.gnome.system.proxy.socks", "host"): "127.0.0.1",
            ("org.gnome.system.proxy.socks", "port"): 7897,
            ("org.gnome.system.proxy", "ignore-hosts"): ["localhost"],
        }
        with patch("codex_wechat.platform_support.getproxies", return_value={}), \
                patch("codex_wechat.platform_support.sys.platform", "linux"), \
                patch("codex_wechat.platform_support._gsettings",
                      side_effect=lambda schema, key: settings[(schema, key)]):
            self.assertEqual(system_proxy(), {
                "ALL_PROXY": "socks5h://127.0.0.1:7897",
                "NO_PROXY": "localhost",
            })

    def test_environment_proxy_wins_over_gnome(self):
        with patch("codex_wechat.platform_support.getproxies",
                   return_value={"https": "http://env-proxy:8080"}), \
                patch("codex_wechat.platform_support.sys.platform", "linux"), \
                patch("codex_wechat.platform_support._gsettings") as settings:
            self.assertEqual(system_proxy(), {"HTTPS_PROXY": "http://env-proxy:8080"})
            settings.assert_not_called()

    def test_detected_proxy_fills_both_schemes_and_keeps_localhost_direct(self):
        env = {}
        with patch("codex_wechat.platform_support.system_proxy", return_value=dict(PROXY)):
            apply_system_proxy(env)
        self.assertEqual(env["HTTPS_PROXY"], "http://127.0.0.1:7897")
        self.assertEqual(env["HTTP_PROXY"], "http://127.0.0.1:7897")
        self.assertIn("127.0.0.1", env["NO_PROXY"])

    def test_an_environment_that_already_names_a_proxy_is_untouched(self):
        env = {"HTTPS_PROXY": "http://localhost:1234"}
        with patch("codex_wechat.platform_support.system_proxy", return_value=dict(PROXY)) as probe:
            apply_system_proxy(env)
        probe.assert_not_called()
        self.assertEqual(env, {"HTTPS_PROXY": "http://localhost:1234"})

    def test_no_detected_proxy_leaves_the_environment_alone(self):
        env = {"PATH": "x"}
        with patch("codex_wechat.platform_support.system_proxy", return_value={}):
            apply_system_proxy(env)
        self.assertEqual(env, {"PATH": "x"})

    def test_all_proxy_also_counts_as_an_explicit_choice(self):
        env = {"ALL_PROXY": "socks5://localhost:1080"}
        with patch("codex_wechat.platform_support.system_proxy", return_value=dict(PROXY)) as probe:
            apply_system_proxy(env)
        probe.assert_not_called()
        self.assertEqual(env, {"ALL_PROXY": "socks5://localhost:1080"})

    def test_backend_environment_carries_the_system_proxy(self):
        spec = ConnectionSpec.from_config({"command": ["codex", "app-server"]})
        with patch("codex_wechat.platform_support.system_proxy", return_value=dict(PROXY)), \
                no_ambient_proxy():
            env = spec.environment({})
        self.assertEqual(env["HTTPS_PROXY"], "http://127.0.0.1:7897")

    def test_backend_config_proxy_wins_over_the_system_proxy(self):
        spec = ConnectionSpec.from_config({"command": ["codex", "app-server"]})
        with patch("codex_wechat.platform_support.system_proxy", return_value=dict(PROXY)):
            env = spec.environment({"env": {"HTTPS_PROXY": "http://user:1080"}})
        self.assertEqual(env["HTTPS_PROXY"], "http://user:1080")

    def test_inheriting_the_system_proxy_is_recorded(self):
        spec = ConnectionSpec.from_config({"command": ["codex", "app-server"]})
        with patch("codex_wechat.platform_support.system_proxy", return_value=dict(PROXY)), \
                no_ambient_proxy(), patch("codex_wechat.codex_connection.log_event") as log:
            spec.environment({})
        log.assert_called_once_with("backend.proxy_inherited", component="backend",
                                    source="system", proxy="127.0.0.1:7897")

    def test_an_explicit_proxy_records_nothing(self):
        spec = ConnectionSpec.from_config({"command": ["codex", "app-server"]})
        with patch("codex_wechat.codex_connection.log_event") as log:
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
                    patch("codex_wechat.platform_support.system_proxy", return_value=dict(PROXY)) as probe:
                env = {"OPENAI_BASE_URL": base}
                self.assertEqual(apply_system_proxy(env), {})
                self.assertEqual(env, {"OPENAI_BASE_URL": base})
                probe.assert_not_called()

    def test_a_remote_base_url_still_gets_the_proxy(self):
        env = {"OPENAI_BASE_URL": "https://api.example.com/v1"}
        with patch("codex_wechat.platform_support.system_proxy", return_value=dict(PROXY)):
            self.assertTrue(apply_system_proxy(env))
        self.assertEqual(env["HTTPS_PROXY"], "http://127.0.0.1:7897")

    def test_local_endpoint_covers_loopback_and_intranet_names(self):
        self.assertTrue(local_endpoint("http://127.0.0.1:1"))
        self.assertTrue(local_endpoint("http://[::1]:1"))
        self.assertTrue(local_endpoint("http://172.16.3.4"))
        self.assertFalse(local_endpoint("https://chatgpt.com/backend-api/codex"))
        self.assertFalse(local_endpoint(""))
        self.assertFalse(local_endpoint("not a url"))

    def test_the_user_can_switch_the_injection_off(self):
        spec = ConnectionSpec.from_config({"command": ["codex", "app-server"],
                                           "useSystemProxy": False})
        with patch("codex_wechat.platform_support.system_proxy", return_value=dict(PROXY)) as probe, \
                patch.dict(os.environ, {
                    "HTTPS_PROXY": "http://ambient:1",
                    "https_proxy": "http://ambient:1",
                    "ALL_PROXY": "socks5://ambient:2",
                }), patch("codex_wechat.codex_connection.log_event") as log:
            env = spec.environment({})
        probe.assert_not_called()
        log.assert_not_called()
        self.assertNotIn("HTTPS_PROXY", env)
        self.assertNotIn("https_proxy", env)
        self.assertNotIn("ALL_PROXY", env)

    def test_disabling_inheritance_keeps_explicit_backend_proxy(self):
        spec = ConnectionSpec.from_config({"command": ["codex", "app-server"],
                                           "useSystemProxy": False})
        with patch.dict(os.environ, {"HTTPS_PROXY": "http://ambient:1"}), \
                patch("codex_wechat.platform_support.system_proxy", return_value=dict(PROXY)):
            env = spec.environment({"env": {"HTTPS_PROXY": "http://explicit:2"}})
        self.assertEqual(env["HTTPS_PROXY"], "http://explicit:2")

    def test_the_switch_defaults_to_on(self):
        spec = ConnectionSpec.from_config({"command": ["codex", "app-server"]})
        self.assertTrue(spec.system_proxy)
        self.assertEqual(ConnectionSpec.from_config(
            {"command": ["codex", "app-server"], "useSystemProxy": True}).system_proxy, True)

    def test_the_switch_rejects_a_non_boolean(self):
        with self.assertRaises(ValueError):
            ConnectionSpec.from_config({"command": ["codex", "app-server"],
                                        "useSystemProxy": "no"})

    def test_an_empty_no_proxy_does_not_leave_loopback_proxied(self):
        env = {"NO_PROXY": ""}
        with patch("codex_wechat.platform_support.system_proxy", return_value=dict(PROXY)):
            apply_system_proxy(env)
        self.assertEqual(env["NO_PROXY"], "localhost,127.0.0.1,::1")

    def test_a_user_no_proxy_is_kept(self):
        env = {"NO_PROXY": "*.corp.example"}
        with patch("codex_wechat.platform_support.system_proxy", return_value=dict(PROXY)):
            apply_system_proxy(env)
        self.assertEqual(env["NO_PROXY"], "*.corp.example")

    def test_a_scheme_less_local_base_url_is_still_local(self):
        self.assertTrue(local_endpoint("127.0.0.1:8080"))
        self.assertTrue(local_endpoint("localhost:11434"))
        self.assertEqual(endpoint_host("localhost:11434"), "localhost")

    def test_an_ipv6_proxy_label_keeps_its_brackets(self):
        self.assertEqual(proxy_label("http://[::1]:8080"), "[::1]:8080")

    def test_an_ambient_proxy_counts_as_an_explicit_choice(self):
        spec = ConnectionSpec.from_config({"command": ["codex", "app-server"]})
        with patch.dict(os.environ, {"HTTPS_PROXY": "http://ambient:1"}), \
                patch("codex_wechat.platform_support.system_proxy", return_value=dict(PROXY)) as probe, \
                patch("codex_wechat.codex_connection.log_event") as log:
            env = spec.environment({})
        probe.assert_not_called()
        log.assert_not_called()
        self.assertEqual(env["HTTPS_PROXY"], "http://ambient:1")


if __name__ == "__main__":
    unittest.main()
