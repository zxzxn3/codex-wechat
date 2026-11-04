"""Every platform is told how to turn the shared daemon on or off."""
import contextlib
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import installer


class NextStepsHintTests(unittest.TestCase):
    def output(self, windows, *, update=False):
        buffer = io.StringIO()
        with patch.object(installer, "IS_WINDOWS", windows), contextlib.redirect_stdout(buffer):
            installer.next_steps(Path("target"), update=update, identifier="identifier")
        return buffer.getvalue()

    def test_both_platforms_name_the_persistent_switch_and_its_defaults(self):
        for windows in (True, False):
            for update in (True, False):
                text = self.output(windows, update=update)
                self.assertIn("/settings shared on", text)
                self.assertIn("Windows 为 false、Linux 为 true", text)


if __name__ == "__main__":
    unittest.main()
