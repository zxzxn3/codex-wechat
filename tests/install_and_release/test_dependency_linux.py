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
