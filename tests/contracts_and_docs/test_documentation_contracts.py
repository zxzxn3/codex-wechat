"""Public documentation routes and executable installation snippets."""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]


class DocumentationContractTests(unittest.TestCase):
    def test_private_install_commands_are_consistent_in_public_docs(self):
        texts = {
            relative: (ROOT / relative).read_text(encoding="utf-8")
            for relative in ("README.md", "docs/INSTALL.md")
        }
        commands = {}
        for relative, text in texts.items():
            self.assertIn("contents/bootstrap.sh?ref=main", text)
            self.assertIn("contents/bootstrap.ps1?ref=main", text)
            self.assertIn("application/vnd.github.raw+json", text)
            self.assertIn("CODEX_WECHAT_TOKEN='github_pat_", text)
            self.assertIn("org.gnome.system.proxy.http", text)
            self.assertIn("$env:CODEX_WECHAT_TOKEN='github_pat_", text)
            # Windows PowerShell 5.1 returns System.Byte[] for the raw Content
            # API media type, so the command must decode before running it.
            self.assertIn("-is [byte[]]", text)
            self.assertIn("[Text.Encoding]::UTF8.GetString", text)
            self.assertNotIn("Invoke-Expression ((Invoke-WebRequest", text)
            self.assertNotIn("HTTPS_PROXY='http://127.0.0.1:7897'", text)
            self.assertNotIn("-Proxy 'http://127.0.0.1:7897'", text)
            self.assertNotIn("read -rsp", text)
            self.assertNotIn("Read-Host", text)
            commands[relative] = (
                next(line for line in text.splitlines()
                     if line.startswith("env CODEX_WECHAT_TOKEN=")),
                next(line for line in text.splitlines()
                     if line.startswith("$env:CODEX_WECHAT_TOKEN=")),
            )
        self.assertEqual(commands["README.md"], commands["docs/INSTALL.md"])
        self.assertIn("## Agent 文档索引", texts["README.md"])

    def test_readme_agent_index_links_exist(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        index = readme.split("## Agent 文档索引", 1)[1].split("\n## ", 1)[0]
        targets = re.findall(r"\[[^]]+\]\(([^)#]+)", index)
        self.assertGreater(len(targets), 10)
        for target in targets:
            with self.subTest(target=target):
                self.assertTrue((ROOT / target).is_file())
