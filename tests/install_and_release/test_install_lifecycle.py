"""One isolated install-to-purge lifecycle across the public contracts."""
import asyncio
import contextlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import installer
from codex_wechat import uninstall
from codex_wechat.install_layout import INSTALLATION_FILE, InstalledLayout


ROOT = Path(__file__).resolve().parents[2]


@unittest.skipIf(os.name == "nt", "Linux lifecycle uses the stable Linux uninstaller")
class InstallationLifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.target = self.base / "install"
        self.outside = self.base / "outside"
        self.outside.mkdir()
        (self.outside / "keep.txt").write_text("keep\n", encoding="utf-8")

    def test_install_status_uninstall_and_purge(self):
        environment = {
            "CODEX_WECHAT_DATA_ROOT": str(self.base / "data"),
            "XDG_CONFIG_HOME": str(self.base / "config"),
            "XDG_STATE_HOME": str(self.base / "state"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "CODEX_WECHAT_BOOTSTRAP_UV": str(
                ROOT / ".bootstrap/uv/0.13.7/uv"),
        }
        install_patches = (
            patch.object(installer, "introduction", return_value=True),
            patch.object(installer, "report_dependencies"),
            patch.object(installer, "preflight_command_link"),
            patch.object(installer, "install_command_link"),
            patch.object(installer, "configure_platform_service"),
            patch.object(installer, "next_steps"),
        )
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, environment))
            inputs = stack.enter_context(patch("builtins.input", return_value="y"))
            for item in install_patches:
                stack.enter_context(item)
            self.assertEqual(installer.install(ROOT, self.target), 0)

        inputs.assert_called_once()
        layout = InstalledLayout.load_required(self.target, platform="linux")
        manifest = json.loads(
            (self.target / INSTALLATION_FILE).read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["releaseId"], layout.release_id)
        self.assertTrue(layout.release_dir().is_dir())
        self.assertTrue(layout.python_executable.is_file())

        runtime = layout.runtime_paths()
        runtime.config_file.parent.mkdir(parents=True, exist_ok=True)
        runtime.config_file.write_text("{}\n", encoding="utf-8")
        runtime.state_dir.mkdir(parents=True, exist_ok=True)
        (runtime.state_dir / "account.json").write_text(
            '{"user": "fixture", "token": "fixture"}\n',
            encoding="utf-8",
        )
        runtime.workspace_dir.mkdir(parents=True, exist_ok=True)
        (runtime.workspace_dir / "note.txt").write_text("note\n", encoding="utf-8")

        status = subprocess.run(
            [str(self.target / "codex-wechat"), "status"],
            cwd=self.base,
            env={**os.environ, **environment},
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(status.returncode, 0, status.stderr)
        payload = json.loads(status.stdout)
        self.assertEqual(payload["state"], "ok")
        self.assertEqual(payload["unsent"], 0)

        no_service = patch.object(uninstall, "_linux_service_stop", AsyncMock())
        no_removal = patch.object(
            uninstall, "_linux_service_removal", AsyncMock())
        no_shared = patch.object(
            uninstall, "_linux_shared_backend_removal", AsyncMock())
        no_command = patch.object(uninstall, "_linux_command_removal")
        with patch.object(uninstall, "INSTALL_ROOT", self.target), \
                no_service, no_removal, no_shared, no_command:
            asyncio.run(uninstall.uninstall())

            self.assertTrue((self.target / INSTALLATION_FILE).is_file())
            self.assertTrue(runtime.config_file.is_file())
            self.assertTrue((runtime.state_dir / "account.json").is_file())
            self.assertTrue((runtime.workspace_dir / "note.txt").is_file())

            asyncio.run(uninstall.uninstall(purge=True))

        self.assertTrue(self.target.is_dir())
        self.assertFalse((self.target / INSTALLATION_FILE).exists())
        self.assertFalse(runtime.config_file.exists())
        self.assertFalse(runtime.state_dir.exists())
        self.assertFalse(runtime.workspace_dir.exists())
        self.assertEqual((self.outside / "keep.txt").read_text(encoding="utf-8"),
                         "keep\n")


if __name__ == "__main__":
    unittest.main()
