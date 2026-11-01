"""Release-pointer contracts for the stable Windows supervisor bootstrap."""
import os
from pathlib import Path
import tempfile
import unittest

import bootstrap_supervisor


class BootstrapSupervisorTests(unittest.TestCase):
    def test_legacy_layout_uses_install_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "windows_service.py").write_text("", encoding="utf-8")
            self.assertEqual(bootstrap_supervisor.active_release(root), root.resolve())

    def test_current_pointer_selects_immutable_release(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release = root / "releases" / "abc123"
            release.mkdir(parents=True)
            (release / "windows_service.py").write_text("", encoding="utf-8")
            (root / "current").write_text("abc123\n", encoding="utf-8")
            self.assertEqual(bootstrap_supervisor.active_release(root), release.resolve())

    def test_invalid_or_incomplete_pointer_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "current").write_text("../escape\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid release id"):
                bootstrap_supervisor.active_release(root)
            (root / "current").write_text("missing\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "incomplete"):
                bootstrap_supervisor.active_release(root)

    @unittest.skipUnless(hasattr(os, "symlink"), "symbolic links unavailable")
    def test_pointer_and_release_paths_reject_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            external = root / "external"
            external.mkdir()
            (root / "current").symlink_to(external / "pointer")
            with self.assertRaisesRegex(ValueError, "pointer must not be a symlink"):
                bootstrap_supervisor.active_release(root)

            (root / "current").unlink()
            outside_releases = root / "outside"
            release = outside_releases / "abc123"
            release.mkdir(parents=True)
            (release / "windows_service.py").write_text("", encoding="utf-8")
            (root / "releases").symlink_to(outside_releases, target_is_directory=True)
            (root / "current").write_text("abc123\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "releases directory must not be a symlink"):
                bootstrap_supervisor.active_release(root)


if __name__ == "__main__":
    unittest.main()
