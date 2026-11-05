"""Static contracts for the thin cross-platform command entries."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class EntryContractTests(unittest.TestCase):
    def test_linux_entry_rejects_root_except_read_only_or_uninstall(self):
        text = (ROOT / "codex-wechat").read_text(encoding="utf-8")
        self.assertIn("CODEX_WECHAT_ALLOW_ROOT", text)
        self.assertIn("status|logs|uninstall", text)
        self.assertIn("不要使用 root/sudo", text)
        self.assertNotIn("desktop)", text)

    def test_linux_uninstall_bypasses_a_broken_current_release(self):
        text = (ROOT / "codex-wechat").read_text(encoding="utf-8")
        self.assertIn('if [ "${1:-}" = "uninstall" ]', text)
        self.assertIn('"$INSTALL_ROOT/uninstall.py"', text)
        self.assertLess(
            text.index('if [ "${1:-}" = "uninstall" ]'),
            text.index('[ -L "$CURRENT_FILE" ]'),
        )
        self.assertIn("CODEX_WECHAT_INSTALL_ROOT", text)

    def test_linux_stable_uninstaller_has_its_runtime_dependencies(self):
        import installer
        for name in (
            "uninstall.py",
            "platform_support.py",
            "service_adapter.py",
            "service_control.py",
            "storage.py",
            "subprocess_io.py",
        ):
            self.assertIn(Path(name), installer.STABLE_LINUX_FILES)

    def test_desktop_entry_rejects_root(self):
        text = (ROOT / "codex-desktop").read_text(encoding="utf-8")
        self.assertIn("CODEX_WECHAT_ALLOW_ROOT", text)
        self.assertIn("不要使用 root/sudo", text)

    def test_windows_public_entry_is_a_python_router(self):
        text = (ROOT / "codex-wechat.ps1").read_text(encoding="utf-8")
        self.assertIn("@($Agent, $Command", text)
        self.assertNotIn("Get-ScheduledTask", text)
        self.assertNotIn("'service-stop'", text)
        self.assertNotIn("'service-status'", text)
        self.assertIn("'autostart'", text)
        self.assertIn("'uninstall'", text)
        self.assertIn("windows/task-service.ps1", (ROOT / "service_adapter.py").read_text(
            encoding="utf-8"))

    def test_task_helper_is_internal_and_owns_native_operations(self):
        text = (ROOT / "windows/task-service.ps1").read_text(encoding="utf-8")
        self.assertIn("ValidateSet('status', 'start', 'stop', 'restart', 'autostart')", text)
        self.assertIn("Start-ScheduledTask", text)
        self.assertIn("Stop-ScheduledTask", text)
        self.assertIn("Wait-ServiceStable", text)
        self.assertNotIn("codex_wechat.py", text)

    def test_task_helper_is_shipped_as_a_stable_windows_file(self):
        import installer
        helper = Path("windows/task-service.ps1")
        self.assertIn(helper, installer.REQUIRED_FILES)
        self.assertIn(helper, installer.STABLE_WINDOWS_FILES)

    def test_linux_service_marks_its_bridge_child(self):
        unit = (ROOT / "codex-wechat.service").read_text(encoding="utf-8")
        self.assertIn("Environment=CODEX_WECHAT_SERVICE_CHILD=1", unit)
