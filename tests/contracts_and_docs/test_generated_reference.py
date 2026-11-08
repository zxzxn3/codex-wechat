"""Generated documentation must match the current command and setting sources."""
import unittest

from scripts.reference_docs import TARGET, render


class GeneratedReferenceTests(unittest.TestCase):
    def test_reference_is_current(self):
        self.assertEqual(TARGET.read_text(encoding="utf-8"), render())

    def test_alias_table_uses_the_command_catalog(self):
        text = render()
        alias_section = text.split("## 微信命令短别名", 1)[1].split("\n## ", 1)[0]
        self.assertIn("| `/a` | `/answer` |", alias_section)
        self.assertIn("| `/v` | `/version` |", alias_section)
