"""Network bootstrap must work with only the downloaded installer present."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

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
