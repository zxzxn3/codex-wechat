"""Patch-debt candidates are deterministic and exclude documentation noise."""
from datetime import datetime, timedelta, timezone
import json
import unittest

from scripts.patch_debt import (
    Commit,
    build_report,
    is_production_path,
    parse_log,
    render_json,
    render_markdown,
)


NOW = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)


def commit(identity, days_ago, subject, *files):
    return Commit(
        identity=identity,
        timestamp=NOW - timedelta(days=days_ago),
        subject=subject,
        files=tuple(files),
    )


class PatchDebtTests(unittest.TestCase):
    def test_parses_git_log_records_with_duplicate_files(self):
        text = (
            "\x1eabc123\x1f2026-11-10T12:00:00+00:00\x1ffix(state): repair\n"
            "codex_wechat/state.py\ncodex_wechat/state.py\ndocs/ARCHITECTURE.md\n"
            "\x1edef456\x1f2026-11-09T12:00:00+00:00\x1ffeat: add\n"
            "codex_wechat/app.py\n"
        )
        commits = parse_log(text)

        self.assertEqual(len(commits), 2)
        self.assertEqual(commits[0].files, (
            "codex_wechat/state.py",
            "docs/ARCHITECTURE.md",
        ))
        self.assertTrue(commits[0].is_fix)
        self.assertFalse(commits[1].is_fix)

    def test_production_filter_keeps_code_and_runtime_configuration(self):
        self.assertTrue(is_production_path("codex_wechat/app.py"))
        self.assertTrue(is_production_path("installer.py"))
        self.assertTrue(is_production_path("config.default.json"))
        self.assertFalse(is_production_path("tests/test_app.py"))
        self.assertFalse(is_production_path("docs/ARCHITECTURE.md"))
        self.assertFalse(is_production_path("assets/logo.png"))

    def test_repeated_recent_fixes_rank_above_isolated_fix(self):
        commits = [
            commit("a" * 40, 2, "fix(state): repair a", "codex_wechat/state.py"),
            commit("b" * 40, 1, "fix(state): repair b", "codex_wechat/state.py"),
            commit("c" * 40, 0, "fix(state): repair c", "codex_wechat/state.py"),
            commit("d" * 40, 0, "feat(state): add", "codex_wechat/state.py"),
            commit("e" * 40, 5, "fix(install): one-off", "installer.py"),
            commit("f" * 40, 3, "fix(docs): only docs", "docs/ARCHITECTURE.md"),
        ]
        report = build_report(commits, days=30, limit=10, now=NOW)

        self.assertEqual([item.path for item in report.candidates], ["codex_wechat/state.py"])
        candidate = report.candidates[0]
        self.assertEqual((candidate.fixes, candidate.changes), (3, 4))
        self.assertEqual(candidate.burst_fixes, 3)
        self.assertEqual(candidate.recent_fixes, 3)
        self.assertEqual(report.production_fix_commits, 4)

    def test_deleted_historical_paths_are_not_current_debt(self):
        commits = [
            commit("a" * 40, 2, "fix: old path", "codex_wechat.py"),
            commit("b" * 40, 1, "fix: old path again", "codex_wechat.py"),
            commit("c" * 40, 1, "fix(state): current", "codex_wechat/state.py"),
            commit("d" * 40, 0, "fix(state): current again", "codex_wechat/state.py"),
        ]
        report = build_report(
            commits,
            days=30,
            limit=10,
            now=NOW,
            current_files={"codex_wechat/state.py"},
        )

        self.assertEqual([item.path for item in report.candidates], ["codex_wechat/state.py"])

    def test_outputs_include_the_candidate_only_disclaimer(self):
        commits = [
            commit("a" * 40, 2, "fix(state): repair a", "codex_wechat/state.py"),
            commit("b" * 40, 1, "fix(state): repair b", "codex_wechat/state.py"),
        ]
        report = build_report(commits, days=30, limit=10, now=NOW)

        markdown = render_markdown(report)
        self.assertIn("不代表当前仍有缺陷", markdown)
        self.assertIn("codex_wechat/state.py", markdown)

        payload = json.loads(render_json(report))
        self.assertEqual(payload["candidate_files"], 1)
        self.assertEqual(payload["candidates"][0]["evidence"][0]["commit"], "b" * 7)

    def test_empty_window_renders_an_explicit_result(self):
        report = build_report([], days=30, limit=10, now=NOW)
        self.assertIn("没有达到阈值的候选", render_markdown(report))


if __name__ == "__main__":
    unittest.main()
