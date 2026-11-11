"""Static contracts for the thin cross-platform command entries."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class EntryContractTests(unittest.TestCase):
    def test_linux_entry_rejects_root_except_read_only_or_uninstall(self):
        text = (ROOT / "codex-wechat").read_text(encoding="utf-8")
        self.assertIn("CODEX_WECHAT_ALLOW_ROOT", text)
        self.assertIn("status|logs|uninstall", text)
        self.assertIn("不要使用 root/sudo", text)
        self.assertNotIn("desktop)", text)

    def test_linux_uninstall_bypasses_the_release_launcher(self):
        text = (ROOT / "codex-wechat").read_text(encoding="utf-8")
        self.assertIn('if [ "$ACTION" = "uninstall" ]', text)
        self.assertIn('"$INSTALL_ROOT/codex_wechat/uninstall.py"', text)
        self.assertIn("-m codex_wechat.uninstall", text)
        self.assertLess(
            text.index('if [ "$ACTION" = "uninstall" ]'),
            text.index("-m codex_wechat.launcher"),
        )
        self.assertIn("CODEX_WECHAT_INSTALL_ROOT", text)

    def test_linux_entry_delegates_layout_resolution_to_the_launcher(self):
        text = (ROOT / "codex-wechat").read_text(encoding="utf-8")
        self.assertIn("-m codex_wechat.launcher", text)
        self.assertIn("--module codex_wechat", text)
        self.assertNotIn("components", text)
        self.assertNotIn("CURRENT_FILE", text)
        self.assertNotIn(".runtime-roots", text)
        self.assertNotIn(".data-root", text)

    def test_linux_stable_uninstaller_has_its_runtime_dependencies(self):
        import installer
        stable = set(installer._stable_files(ROOT))
        for path in (
            Path("codex_wechat/uninstall.py"),
            Path("codex_wechat/install_layout.py"),
            Path("codex_wechat/launcher.py"),
            Path("codex_wechat/platform_support.py"),
            Path("codex_wechat/service_adapter.py"),
            Path("codex_wechat/service_control.py"),
            Path("codex_wechat/storage.py"),
            Path("codex_wechat/subprocess_io.py"),
        ):
            self.assertIn(path, stable)

    def test_python_entries_use_the_safe_path_option(self):
        for relative in ("codex-wechat", "codex-desktop",
                         "codex-wechat.ps1", "codex-desktop.ps1",
                         "install.ps1", "uninstall.ps1"):
            with self.subTest(relative=relative):
                self.assertIn("-P", (ROOT / relative).read_text(encoding="utf-8"))

    def test_desktop_entry_rejects_root(self):
        text = (ROOT / "codex-desktop").read_text(encoding="utf-8")
        self.assertIn("CODEX_WECHAT_ALLOW_ROOT", text)
        self.assertIn("不要使用 root/sudo", text)

    def test_public_entry_does_not_expose_the_internal_cli(self):
        text = (ROOT / "codex-wechat").read_text(encoding="utf-8")
        self.assertNotIn('exec "$INSTALL_ROOT/codex-desktop"', text)
        self.assertNotIn('ACTION="$1"; shift', text)
        self.assertTrue((ROOT / "codex-desktop").is_file())

    def test_windows_public_entry_is_a_python_router(self):
        text = (ROOT / "codex-wechat.ps1").read_text(encoding="utf-8")
        self.assertIn("'codex_wechat.launcher'", text)
        self.assertIn("'--module', 'codex_wechat'", text)
        self.assertNotIn("Get-ScheduledTask", text)
        self.assertNotIn("'service-stop'", text)
        self.assertNotIn("'service-status'", text)
        self.assertIn("'autostart'", text)
        self.assertIn("'uninstall'", text)
        self.assertNotIn("'components'", text)
        self.assertIn("windows/task-service.ps1",
                      (ROOT / "codex_wechat/service_adapter.py").read_text(
            encoding="utf-8"))
        self.assertNotIn("Read-SavedDataRoot", text)
        self.assertNotIn("当前版本指针 current", text)

    def test_windows_installer_actions_only_forward_local_source(self):
        text = (ROOT / "codex-wechat.ps1").read_text(encoding="utf-8")
        self.assertIn("[string]$Source", text)
        self.assertIn(
            "if ($Source) { $InstallerArguments += @('--source', $Source) }",
            text,
        )
        self.assertIn("$Source -or $DryRun -or $NonInteractive", text)
        self.assertNotIn("[string]$Repo", text)
        self.assertNotIn("[string]$Ref", text)

    def test_windows_entry_exposes_help_and_uninstall_force(self):
        text = (ROOT / "codex-wechat.ps1").read_text(encoding="utf-8")
        self.assertIn("[Alias('h')]", text)
        self.assertIn("[switch]$Help", text)
        self.assertIn("if ($Help)", text)
        self.assertIn("[switch]$Force", text)
        self.assertIn("if ($Force -and $Command -ne 'uninstall')", text)
        self.assertIn("$UninstallerArguments.Force = $true", text)
        self.assertLess(text.index("if ($Help)"), text.index("if ($Command -notin"))

    def test_task_helper_is_internal_and_owns_native_operations(self):
        text = (ROOT / "windows/task-service.ps1").read_text(encoding="utf-8")
        self.assertIn("ValidateSet('status', 'start', 'stop', 'autostart')", text)
        self.assertIn("Start-ScheduledTask", text)
        self.assertIn("Stop-ScheduledTask", text)
        self.assertNotIn("'restart'", text)
        self.assertNotIn("Wait-ServiceStable", text)
        self.assertNotIn("Wait-TaskStopped", text)
        self.assertNotIn("codex_wechat.py", text)

    def test_task_helper_is_shipped_as_a_stable_windows_file(self):
        import installer
        helper = Path("windows/task-service.ps1")
        self.assertIn(helper, installer.REQUIRED_FILES)
        self.assertIn(helper, installer.STABLE_WINDOWS_FILES)

    def test_linux_service_marks_its_bridge_child(self):
        unit = (ROOT / "codex-wechat.service").read_text(encoding="utf-8")
        self.assertIn("Environment=CODEX_WECHAT_SERVICE_CHILD=1", unit)
        self.assertNotIn("ConditionPathExists", unit)
