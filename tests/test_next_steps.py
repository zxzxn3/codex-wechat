"""Every platform is told how to turn the shared daemon on or off."""
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

    def test_both_platforms_name_the_persistent_switch_and_its_defaults(self):
        for windows in (True, False):
            for update in (True, False):
                text = self.output(windows, update=update)
                self.assertIn("/settings shared true", text)
                self.assertIn("Windows 为 false、Linux 为 true", text)

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
