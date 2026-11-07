"""Installer next-step hints."""
import contextlib
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import installer


class NextStepsHintTests(unittest.TestCase):
    def output(self, windows, *, update=False, service_ready=True):
        buffer = io.StringIO()
        with patch.object(installer, "IS_WINDOWS", windows), contextlib.redirect_stdout(buffer):
            installer.next_steps(Path("target"), update=update, identifier="identifier",
                                 service_ready=service_ready)
        return buffer.getvalue()

    def test_fresh_install_offers_autostart_without_the_shared_hint(self):
        for windows in (True, False):
            text = self.output(windows)
            self.assertIn("可选功能:", text)
            self.assertIn("- 微信中发送 /settings autostart true 打开开机自启", text)
            self.assertNotIn("/settings shared true", text)

    def test_linux_install_recommends_service_start_and_preserves_autostart(self):
        install = self.output(False)
        update = self.output(False, update=True)
        self.assertIn("service-start", install)
        self.assertNotIn("后台服务已注册", install)
        self.assertNotIn("保持此终端打开", install)
        self.assertIn("现有自启状态保持不变", update)

    def test_windows_first_install_recommends_service_start(self):
        text = self.output(True)
        self.assertIn("首次运行", text)
        self.assertIn("service-start", text)

    def test_windows_fallback_uses_execution_policy_bypass(self):
        with patch.object(
            installer, "windows_command_state",
            return_value=(Path("target/codex-wechat.cmd"), "not-on-path", ""),
        ):
            text = self.output(True)
        self.assertIn(
            "powershell -NoProfile -ExecutionPolicy Bypass -File",
            text,
        )

    def test_linux_install_reports_when_service_registration_was_unavailable(self):
        text = self.output(False, service_ready=False)
        self.assertIn("后台服务未注册", text)
        self.assertNotIn("后台服务已注册", text)

    def test_linux_update_without_service_does_not_recommend_restart(self):
        text = self.output(False, update=True, service_ready=False)
        self.assertIn("后台服务未注册", text)
        self.assertNotIn("systemctl --user restart", text)
        self.assertNotIn("service-start", text)


if __name__ == "__main__":
    unittest.main()
