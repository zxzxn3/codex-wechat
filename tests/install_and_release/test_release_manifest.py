"""Old release identity is independent of a newer source packaging policy."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import installer

class ReleaseManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name)
        self.source=self.base/"source"
        self.source.mkdir()
        self.target=self.base/"app"
        for name in installer.REQUIRED_FILES:
            p=self.source/name
            p.parent.mkdir(parents=True,exist_ok=True)
            p.write_text("fixture",encoding="utf-8")
        self.files=installer.project_files(self.source)
        old=self.source/"experiments/old-example.txt"
        old.parent.mkdir()
        old.write_text("old public file",encoding="utf-8")
        self.files[Path("experiments/old-example.txt")]=(old.read_bytes(),old.stat().st_mode & 0o777)
        self.identifier=installer.release_identifier(self.files)
        self.release=installer.release_path(self.target,self.identifier)
        installer._copy_release(self.release,self.files)
    def test_new_exclusions_do_not_reject_an_old_valid_release(self):
        with patch.object(installer,"PRIVATE_NAMES",installer.PRIVATE_NAMES|{"experiments"}):
            self.assertTrue(installer.release_matches(self.target,self.identifier))
    def test_new_source_requirements_do_not_redefine_old_release(self):
        with patch.object(installer,"REQUIRED_FILES",installer.REQUIRED_FILES|{Path("new-module.py")}):
            self.assertTrue(installer.release_matches(self.target,self.identifier))
    def test_excluded_but_declared_old_file_is_still_checked(self):
        (self.release/"experiments/old-example.txt").write_text("tampered",encoding="utf-8")
        self.assertFalse(installer.release_matches(self.target,self.identifier))
    def test_undeclared_program_file_is_rejected(self):
        (self.release/"injected.py").write_text("fixture",encoding="utf-8")
        self.assertFalse(installer.release_matches(self.target,self.identifier))
    def test_manifest_cannot_escape_release_root(self):
        path=self.release/installer.MANIFEST_FILE
        manifest=json.loads(path.read_text(encoding="utf-8"))
        manifest["files"].insert(0,"../outside")
        path.write_text(json.dumps(manifest),encoding="utf-8")
        self.assertFalse(installer.release_matches(self.target,self.identifier))
