"""Static contracts for the thin cross-platform command entries."""
import os
from pathlib import Path
import subprocess
import tempfile
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

    def test_desktop_entry_rejects_root(self):
        text = (ROOT / "codex-desktop").read_text(encoding="utf-8")
        self.assertIn("CODEX_WECHAT_ALLOW_ROOT", text)
        self.assertIn("不要使用 root/sudo", text)

    def test_python_entries_ignore_ambient_import_paths(self):
        for relative in ("codex-wechat", "codex-desktop"):
            with self.subTest(relative=relative):
                text = (ROOT / relative).read_text(encoding="utf-8")
                self.assertIn("-u PYTHONPATH", text)
                self.assertIn("-u PYTHONHOME", text)
                self.assertIn("-s -P", text)
        text = (ROOT / "bootstrap.sh").read_text(encoding="utf-8")
        self.assertIn("unset PYTHONPATH PYTHONHOME", text)
        self.assertIn('"$python_command" -I', text)
        install = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn("unset PYTHONPATH PYTHONHOME", install)
        self.assertIn('"$python_command" -I', install)

    def test_install_sh_ignores_ambient_python_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ambient = root / "ambient"
            ambient.mkdir()
            (ambient / "sitecustomize.py").write_text(
                "import os\n"
                "from pathlib import Path\n"
                "Path(os.environ['CODEX_WECHAT_AMBIENT_MARKER']).write_text('hit')\n",
                encoding="utf-8",
            )
            marker = root / "marker"
            env = os.environ.copy()
            env.update({
                "PYTHONPATH": str(ambient),
                "PYTHONHOME": str(root / "home"),
                "CODEX_WECHAT_AMBIENT_MARKER": str(marker),
            })
            result = subprocess.run(
                [str(ROOT / "install.sh"), "--dry-run"],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertFalse(marker.exists())

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
        self.assertIn("windows/task-service.ps1",
                      (ROOT / "codex_wechat/service_adapter.py").read_text(
            encoding="utf-8"))
        self.assertNotIn("Read-SavedDataRoot", text)
        self.assertNotIn("当前版本指针 current", text)

    def test_windows_installer_actions_forward_a_local_source(self):
        text = (ROOT / "codex-wechat.ps1").read_text(encoding="utf-8")
        self.assertIn("[string]$Source", text)
        self.assertIn(
            "if ($Source) { $InstallerArguments += @('--source', $Source) }",
            text,
        )
        self.assertIn("$Repo -or $Ref -or $Source", text)

    def test_windows_entry_exposes_help_and_uninstall_force(self):
        text = (ROOT / "codex-wechat.ps1").read_text(encoding="utf-8")
        self.assertIn("[Alias('h')]", text)
        self.assertIn("[switch]$Help", text)
        self.assertIn("if ($Help)", text)
        self.assertIn("[switch]$Force", text)
        self.assertIn("if ($Force -and $Command -ne 'uninstall')", text)
        self.assertIn("$UninstallerArguments.Force = $true", text)
        self.assertLess(text.index("if ($Help)"), text.index("if ($Command -notin"))

    def test_windows_entries_ignore_ambient_python_environment(self):
        text = (ROOT / "codex-wechat.ps1").read_text(encoding="utf-8")
        self.assertIn("function Invoke-IsolatedPython", text)
        self.assertIn("Remove-Item Env:PYTHONPATH", text)
        self.assertIn("Remove-Item Env:PYTHONHOME", text)
        self.assertIn("& $Python -I @Arguments", text)
        desktop = (ROOT / "codex-desktop.ps1").read_text(encoding="utf-8")
        self.assertIn("Remove-Item Env:PYTHONPATH", desktop)
        self.assertIn("Remove-Item Env:PYTHONHOME", desktop)
        self.assertIn("-s -P", desktop)
        bootstrap = (ROOT / "bootstrap.ps1").read_text(encoding="utf-8")
        self.assertIn("Remove-Item Env:PYTHONPATH", bootstrap)
        self.assertIn("Remove-Item Env:PYTHONHOME", bootstrap)
        self.assertIn("-I -u @InstallerArguments", bootstrap)

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
