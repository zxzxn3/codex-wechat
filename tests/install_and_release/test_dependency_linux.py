"""Platform dependency policy belongs to the shared installer, not the shell adapter."""
import contextlib
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import installer


class DependencyPolicyTests(unittest.TestCase):
    def test_missing_runtime_tools_warn_without_blocking(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(installer.shutil, "which", return_value=None), \
             patch.object(installer.subprocess, "run") as run, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            installer.report_dependencies(Path(directory))
            self.assertIn("可稍后补齐", output.getvalue())
            run.assert_not_called()

    def test_old_curl_warns_and_dry_run_does_not_execute_version_commands(self):
        declaration = (("curl", "curl", (8, 4), "微信收发", "warn"),)
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(installer, "DEPENDENCIES", declaration), \
             patch.object(installer.shutil, "which", return_value="/fake/curl"), \
             patch.object(installer.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "curl 8.3.0", "")) as run, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            installer.report_dependencies(Path(directory))
            self.assertIn("版本不足", output.getvalue())
            run.reset_mock()
            installer.report_dependencies(Path(directory), dry_run=True)
            run.assert_not_called()

    def test_component_absence_does_not_enter_core_install(self):
        with patch.object(installer, "_common_install", return_value=0) as core, \
             patch.object(installer, "component_install") as component, \
             patch.object(installer, "default_target", return_value=Path('/unread/target')):
            self.assertEqual(installer.main(["install", "--dry-run", "--non-interactive"]), 0)
            core.assert_called_once()
            component.assert_not_called()
