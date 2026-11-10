"""Static contracts for the thin cross-platform command entries."""
import json
import os
from pathlib import Path
import shutil
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

    def test_linux_uninstall_bypasses_a_broken_current_release(self):
        text = (ROOT / "codex-wechat").read_text(encoding="utf-8")
        self.assertIn('if [ "$ACTION" = "uninstall" ]', text)
        self.assertIn('"$INSTALL_ROOT/codex_wechat/uninstall.py"', text)
        self.assertIn("-m codex_wechat.uninstall", text)
        self.assertLess(
            text.index('if [ "$ACTION" = "uninstall" ]'),
            text.index('[ -L "$CURRENT_FILE" ]'),
        )
        self.assertIn("CODEX_WECHAT_INSTALL_ROOT", text)

    def test_linux_entry_preserves_an_explicit_data_root(self):
        text = (ROOT / "codex-wechat").read_text(encoding="utf-8")
        self.assertIn('export CODEX_WECHAT_DATA_ROOT="${CODEX_WECHAT_DATA_ROOT:-$INSTALL_ROOT}"', text)
        self.assertNotIn('export CODEX_WECHAT_DATA_ROOT="$INSTALL_ROOT"', text)
        self.assertIn("DATA_ROOT_FILE", text)
        self.assertIn("RUNTIME_ROOTS_FILE", text)
        self.assertIn(".runtime-roots", text)
        self.assertIn(".data-root", text)
        self.assertIn("不一致", text)
        self.assertLess(text.index("不一致"), text.index('if [ "$ACTION" = "uninstall" ]'))
        self.assertIn("已安装布局缺少运行时路径标记", text)

    @unittest.skipIf(os.name == "nt", "POSIX launcher integration")
    def test_linux_entry_accepts_the_atomic_manifest_without_legacy_markers(self):
        with tempfile.TemporaryDirectory() as directory:
            install = Path(directory) / "install"
            release_id = "a" * 16
            release = install / "releases" / release_id
            (release / "codex_wechat").mkdir(parents=True)
            (release / "codex_wechat/app.py").write_text("", encoding="utf-8")
            (install / "current").write_text(release_id + "\n", encoding="utf-8")
            (install / ".runtime-roots").write_text(json.dumps({
                "version": 1,
                "data": str(Path(directory) / "custom-data"),
                "config": str(Path(directory) / "config/codex-wechat"),
                "state": str(Path(directory) / "state/codex-wechat"),
            }) + "\n", encoding="utf-8")
            fake_python = release / ".venv/bin/python"
            fake_python.parent.mkdir(parents=True)
            fake_python.write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' \"${CODEX_WECHAT_DATA_ROOT-<unset>}\"\n",
                encoding="utf-8",
            )
            fake_python.chmod(0o755)
            shutil.copy2(ROOT / "codex-wechat", install / "codex-wechat")
            environment = {key: value for key, value in os.environ.items()
                           if key != "CODEX_WECHAT_DATA_ROOT"}
            result = subprocess.run(
                [install / "codex-wechat", "status"],
                env=environment,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "<unset>")

    def test_linux_stable_uninstaller_has_its_runtime_dependencies(self):
        import installer
        stable = set(installer._stable_files(ROOT))
        for path in (
            Path("codex_wechat/uninstall.py"),
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

    def test_public_entry_does_not_expose_the_internal_cli(self):
        text = (ROOT / "codex-wechat").read_text(encoding="utf-8")
        self.assertNotIn('exec "$INSTALL_ROOT/codex-desktop"', text)
        self.assertNotIn('ACTION="$1"; shift', text)
        self.assertTrue((ROOT / "codex-desktop").is_file())

    def test_windows_public_entry_is_a_python_router(self):
        text = (ROOT / "codex-wechat.ps1").read_text(encoding="utf-8")
        self.assertIn("@('-m', 'codex_wechat', $Command", text)
        self.assertNotIn("Get-ScheduledTask", text)
        self.assertNotIn("'service-stop'", text)
        self.assertNotIn("'service-status'", text)
        self.assertIn("'autostart'", text)
        self.assertIn("'uninstall'", text)
        self.assertIn("windows/task-service.ps1",
                      (ROOT / "codex_wechat/service_adapter.py").read_text(
            encoding="utf-8"))
        self.assertIn("已安装布局缺少数据根标记", text)

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
