"""Release pruning contracts, independent of installer prompts."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import installer


class ReleasePruningTests(unittest.TestCase):
    def test_active_release_is_never_pruned(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            releases = target / "releases"
            for name in ("r1", "r2", "r3"):
                (releases / name).mkdir(parents=True)
            (target / "active-release").write_text("r1\n", encoding="utf-8")
            with patch.object(installer, "PRUNE_RETRY_SECONDS", 0):
                removed = installer.prune_releases(target, keep=("r3",))
            self.assertEqual(removed, ["r2"])
            self.assertEqual(sorted(path.name for path in releases.iterdir()), ["r1", "r3"])

    def test_stale_trash_is_removed_before_pruning(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            releases = target / "releases"
            (releases / ".trash-old").mkdir(parents=True)
            (releases / "keep").mkdir()
            with patch.object(installer, "PRUNE_RETRY_SECONDS", 0):
                self.assertEqual(installer.prune_releases(target, keep=("keep",)), [])
            self.assertFalse((releases / ".trash-old").exists())


if __name__ == "__main__":
    unittest.main()
