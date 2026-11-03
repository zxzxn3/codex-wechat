"""Network bootstrap must work with only the downloaded installer present."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import installer


class StandaloneInstallerTests(unittest.TestCase):
    def test_help_and_bootstrap_dry_run_need_no_adjacent_modules(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root/'installer.py'
            shutil.copy2(Path(installer.__file__), script)
            env = dict(os.environ, HOME=str(root), USERPROFILE=str(root), LOCALAPPDATA=str(root))
            for arguments in (['--help'], ['bootstrap', '--repo', 'https://github.com/example/project',
                                          '--dir', str(root/'target'), '--dry-run', '--non-interactive']):
                with self.subTest(arguments=arguments):
                    result = subprocess.run([sys.executable, '-B', str(script), *arguments],
                                            env=env, cwd=root, capture_output=True, text=True, timeout=15)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertNotIn('ModuleNotFoundError', result.stderr)
            self.assertFalse((root/'target').exists())
            self.assertEqual({p.name for p in root.iterdir()}, {'installer.py'})

    def test_native_qr_avoids_installing_the_fallback(self):
        for native in (None, '/native/qrencode'):
            with self.subTest(native=native), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                for name in installer.REQUIRED_FILES:
                    path=root/name
                    path.parent.mkdir(parents=True,exist_ok=True)
                    path.write_text('fixture')
                python=installer._venv_python(root)
                python.parent.mkdir(parents=True)
                python.touch()
                with patch.object(installer, '_run') as run, \
                        patch.object(installer.shutil, 'which', return_value=native):
                    installer.prepare_runtime(root)
                commands=[call.args[0] for call in run.call_args_list]
                fallback=[command for command in commands if str(root/installer.QR_FALLBACK_REQUIREMENTS) in command]
                self.assertEqual(len(fallback), 0 if native else 1)
