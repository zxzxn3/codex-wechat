"""Public documentation routes and executable installation snippets."""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]


class DocumentationContractTests(unittest.TestCase):
    def test_public_install_commands_are_consistent_in_public_docs(self):
        texts = {
            relative: (ROOT / relative).read_text(encoding="utf-8")
            for relative in ("README.md", "docs/INSTALL.md")
        }
        commands = {}
        for relative, text in texts.items():
            self.assertIn(
                "raw.githubusercontent.com/zxzxn3/codex-wechat/main/bootstrap.sh",
                text,
            )
            self.assertIn(
                "raw.githubusercontent.com/zxzxn3/codex-wechat/main/bootstrap.ps1",
                text,
            )
            for forbidden_term in (
                "\u5185\u6d4b",
                "\u79c1\u6709\u4ed3\u5e93",
                "CODEX_" "WECHAT_TOKEN",
                "github_" "pat_",
            ):
                self.assertNotIn(forbidden_term, text)
            commands[relative] = (
                next(line for line in text.splitlines()
                     if line.startswith("curl -fsSL ")),
                next(line for line in text.splitlines()
                     if line.startswith("irm ")),
            )
        self.assertEqual(commands["README.md"], commands["docs/INSTALL.md"])
        self.assertIn("[文档索引](docs/INDEX.md)", texts["README.md"])

    def test_documentation_index_links_exist(self):
        index_path = ROOT / "docs" / "INDEX.md"
        index = index_path.read_text(encoding="utf-8")
        targets = re.findall(r"\[[^]]+\]\(([^)#]+)", index)
        self.assertGreater(len(targets), 10)
        for target in targets:
            with self.subTest(target=target):
                self.assertTrue((index_path.parent / target).is_file())
