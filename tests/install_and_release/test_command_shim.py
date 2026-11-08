"""Per-user command shim: one short name next to the Linux symlink."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import installer


ENTRY = Path(r"C:\Users\demo\AppData\Local\codex-wechat\app\codex-wechat.ps1")


class ShimTextTests(unittest.TestCase):
    def test_shim_bypasses_the_policy_and_forwards_arguments(self):
        text = installer.windows_shim_text(ENTRY)
        self.assertIn(installer.WINDOWS_SHIM_MARKER, text)
        self.assertIn("-ExecutionPolicy Bypass", text)
        self.assertIn(f'-File "{ENTRY}"', text)
        self.assertIn("%*", text)
        self.assertTrue(text.startswith("@echo off\r\n"))

    def test_shim_propagates_the_exit_code(self):
        # The service commands rely on 78 for configuration failures.
        self.assertIn("exit /b %ERRORLEVEL%", installer.windows_shim_text(ENTRY))


class PathEntryTests(unittest.TestCase):
    def test_matches_regardless_of_separator_case_and_quotes(self):
        directory = r"C:\Users\demo\.local\bin"
        for value in (
            r"C:\Users\demo\.local\bin",
            r"C:\Users\demo\.local\bin\\",
            r"c:\users\demo\.local\bin",
            "C:/Users/demo/.local/bin",
            r'"C:\Users\demo\.local\bin"',
            r"C:\Windows;C:\Users\demo\.local\bin;C:\Tools",
        ):
            with self.subTest(value=value):
                self.assertTrue(installer.path_entry_matches(directory, value))

    def test_rejects_lookalikes_and_empty_input(self):
        directory = r"C:\Users\demo\.local\bin"
        for value in (
            r"C:\Users\demo\.local\bin2",
            r"C:\Users\demo\.local",
            r"C:\Users\demo\bin",
            "",
            None,
        ):
            with self.subTest(value=value):
                self.assertFalse(installer.path_entry_matches(directory, value))

    def test_expands_registry_style_environment_variables(self):
        with patch.dict("os.environ", {"USERPROFILE": r"C:\Users\demo"}):
            self.assertTrue(installer.path_entry_matches(
                r"C:\Users\demo\.local\bin",
                r"%USERPROFILE%\.local\bin",
            ))


class WindowsCommandStateTests(unittest.TestCase):
    def _state(self, *, local_bin_exists=True, local_bin_on_path=True, shim_text=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local_bin = root / ".local" / "bin"
            if local_bin_exists:
                local_bin.mkdir(parents=True)
            if shim_text is not None:
                (local_bin / installer.WINDOWS_SHIM_NAME).write_text(shim_text, encoding="utf-8")
            target = root / "app"
            target.mkdir()
            path_value = str(local_bin) if local_bin_on_path else r"C:\Windows"
            with patch.object(installer, "_windows_local_bin", return_value=local_bin), \
                    patch.object(installer, "_windows_path_value", return_value=path_value):
                shim, status, message = installer.windows_command_state(target)
        return shim, status, message

    def test_missing_directory_degrades_instead_of_failing(self):
        _, status, message = self._state(local_bin_exists=False)
        self.assertEqual(status, "missing-directory")
        self.assertIn("完整路径", message)

    def test_directory_outside_path_degrades_and_never_edits_path(self):
        _, status, message = self._state(local_bin_on_path=False)
        self.assertEqual(status, "not-on-path")
        self.assertIn("不会修改 PATH", message)

    def test_ready_when_the_directory_is_on_path(self):
        _, status, message = self._state()
        self.assertEqual(status, "ready")
        self.assertIn("codex-wechat.cmd", message)

    def test_an_owned_shim_stays_ready(self):
        _, status, _ = self._state(shim_text=installer.windows_shim_text(ENTRY))
        self.assertEqual(status, "ready")

    def test_a_foreign_file_is_never_adopted(self):
        _, status, message = self._state(shim_text="@echo off\r\necho someone else\r\n")
        self.assertEqual(status, "foreign")
        self.assertIn("请先自行处理", message)


class WindowsInstallTests(unittest.TestCase):
    def _install(self, *, local_bin_exists=True, local_bin_on_path=True):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        local_bin = root / ".local" / "bin"
        if local_bin_exists:
            local_bin.mkdir(parents=True)
        target = root / "app"
        target.mkdir()
        path_value = str(local_bin) if local_bin_on_path else r"C:\Windows"
        patches = [
            patch.object(installer, "IS_WINDOWS", True),
            patch.object(installer, "_windows_local_bin", return_value=local_bin),
            patch.object(installer, "_windows_path_value", return_value=path_value),
            patch.object(installer, "detail"),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        return local_bin / installer.WINDOWS_SHIM_NAME, target

    def test_preflight_tolerates_a_missing_directory(self):
        self._install(local_bin_exists=False)
        installer.preflight_command_link(Path("/nowhere"))

    def test_preflight_still_refuses_a_foreign_entry(self):
        shim, target = self._install()
        shim.write_text("@echo off\r\necho someone else\r\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "不是本项目的命令入口"):
            installer.preflight_command_link(target)

    def test_install_writes_the_shim_and_is_idempotent(self):
        shim, target = self._install()
        installer.install_command_link(target)
        first = shim.read_text(encoding="utf-8")
        self.assertIn(installer.WINDOWS_SHIM_MARKER, first)
        self.assertIn(str(target / "codex-wechat.ps1"), first)
        stamp = shim.stat().st_mtime_ns
        installer.install_command_link(target)
        self.assertEqual(shim.read_text(encoding="utf-8"), first)
        self.assertEqual(shim.stat().st_mtime_ns, stamp)

    def test_install_leaves_no_file_when_the_directory_is_unusable(self):
        shim, target = self._install(local_bin_on_path=False)
        installer.install_command_link(target)
        self.assertFalse(shim.exists())
