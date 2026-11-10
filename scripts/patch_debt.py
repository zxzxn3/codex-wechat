#!/usr/bin/env python3
"""Rank production files that the fix history marks as patch-debt candidates."""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
RECORD_SEPARATOR = "\x1e"
FIELD_SEPARATOR = "\x1f"
FIX_SUBJECT = re.compile(r"^fix(?:\([^)]+\))?!?:\s+\S", re.IGNORECASE)
EXCLUDED_PREFIXES = (
    ".github/",
    "assets/",
    "copyright/",
    "docs/",
    "releases/",
    "tests/",
)
EXCLUDED_FILES = {
    ".gitignore",
    "LICENSE",
    "NOTICE",
    "README.md",
    "TRADEMARKS.md",
}
PRODUCTION_SUFFIXES = (
    ".json",
    ".ps1",
    ".py",
    ".service",
    ".sh",
    ".toml",
    ".yaml",
    ".yml",
)
PRODUCTION_NAMES = {"codex-wechat"}
MIN_FIXES = 2
BURST_DAYS = 7
RECENT_DAYS = 14
EVIDENCE_LIMIT = 3


@dataclass(frozen=True)
class Commit:
    identity: str
    timestamp: datetime
    subject: str
    files: tuple[str, ...]

    @property
    def is_fix(self) -> bool:
        return bool(FIX_SUBJECT.match(self.subject))


@dataclass(frozen=True)
class Evidence:
    identity: str
    timestamp: str
    subject: str


@dataclass(frozen=True)
class Candidate:
    path: str
    score: int
    fixes: int
    changes: int
    burst_fixes: int
    recent_fixes: int
    last_fix: str
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True)
class Report:
    since: str
    until: str
    days: int
    commits: int
    fix_commits: int
    production_fix_commits: int
    candidate_files: int
    candidates: tuple[Candidate, ...]


def is_production_path(path: str) -> bool:
    """Keep code and runtime configuration files out of docs/test noise."""
    normalized = path.removeprefix("./")
    if normalized.startswith(EXCLUDED_PREFIXES) or normalized in EXCLUDED_FILES:
        return False
    return normalized.endswith(PRODUCTION_SUFFIXES) or normalized in PRODUCTION_NAMES


def parse_log(text: str) -> list[Commit]:
    """Parse records emitted with RECORD_SEPARATOR/FIELD_SEPARATOR headers."""
    commits: list[Commit] = []
    for record in text.split(RECORD_SEPARATOR):
        lines = record.splitlines()
        if not lines:
            continue
        fields = lines[0].split(FIELD_SEPARATOR, 2)
        if len(fields) != 3:
            continue
        identity, raw_timestamp, subject = fields
        commits.append(Commit(
            identity=identity,
            timestamp=datetime.fromisoformat(raw_timestamp),
            subject=subject,
            files=tuple(dict.fromkeys(line.strip() for line in lines[1:] if line.strip())),
        ))
    return commits


def read_commits(repo: Path, *, since: datetime) -> list[Commit]:
    text = subprocess.check_output(
        [
            "git",
            "-C",
            str(repo),
            "log",
            "--no-merges",
            "--no-renames",
            f"--since={since.isoformat()}",
            f"--format={RECORD_SEPARATOR}%H{FIELD_SEPARATOR}%aI{FIELD_SEPARATOR}%s",
            "--name-only",
            "--",
        ],
        text=True,
        encoding="utf-8",
    )
    return parse_log(text)


def read_current_files(repo: Path) -> set[str]:
    raw_paths = subprocess.check_output(
        ["git", "-C", str(repo), "ls-files", "-z", "--cached"],
    )
    return {
        path for raw_path in raw_paths.split(b"\0")
        if (path := raw_path.decode("utf-8")) and (repo / path).is_file()
    }


def _max_burst(timestamps: list[datetime], window: timedelta) -> int:
    ordered = sorted(timestamps)
    best = 0
    left = 0
    for right, timestamp in enumerate(ordered):
        while timestamp - ordered[left] > window:
            left += 1
        best = max(best, right - left + 1)
    return best


def build_report(
    commits: list[Commit],
    *,
    days: int,
    limit: int,
    now: datetime,
    current_files: set[str] | None = None,
) -> Report:
    since = now - timedelta(days=days)
    recent_since = now - timedelta(days=RECENT_DAYS)
    burst_window = timedelta(days=BURST_DAYS)
    window_commits = [commit for commit in commits if since <= commit.timestamp <= now]
    changes: dict[str, int] = defaultdict(int)
    fixes: dict[str, list[Commit]] = defaultdict(list)
    production_fix_commits = 0

    for commit in window_commits:
        production_files = {
            path for path in commit.files
            if is_production_path(path)
            and (current_files is None or path in current_files)
        }
        for path in production_files:
            changes[path] += 1
        if not commit.is_fix or not production_files:
            continue
        production_fix_commits += 1
        for path in production_files:
            fixes[path].append(commit)

    candidates: list[Candidate] = []
    for path, path_fixes in fixes.items():
        if len(path_fixes) < MIN_FIXES:
            continue
        timestamps = [commit.timestamp for commit in path_fixes]
        burst_fixes = _max_burst(timestamps, burst_window)
        recent_fixes = sum(timestamp >= recent_since for timestamp in timestamps)
        fix_count = len(path_fixes)
        score = fix_count * 2 + burst_fixes * 3 + recent_fixes * 2
        latest = sorted(path_fixes, key=lambda commit: commit.timestamp, reverse=True)
        candidates.append(Candidate(
            path=path,
            score=score,
            fixes=fix_count,
            changes=changes[path],
            burst_fixes=burst_fixes,
            recent_fixes=recent_fixes,
            last_fix=latest[0].timestamp.isoformat(),
            evidence=tuple(
                Evidence(
                    identity=commit.identity[:7],
                    timestamp=commit.timestamp.isoformat(),
                    subject=commit.subject,
                )
                for commit in latest[:EVIDENCE_LIMIT]
            ),
        ))

    candidates.sort(
        key=lambda item: (
            item.score,
            item.fixes,
            item.burst_fixes,
            item.recent_fixes,
            item.last_fix,
            item.path,
        ),
        reverse=True,
    )
    return Report(
        since=since.isoformat(),
        until=now.isoformat(),
        days=days,
        commits=len(window_commits),
        fix_commits=sum(commit.is_fix for commit in window_commits),
        production_fix_commits=production_fix_commits,
        candidate_files=len(candidates),
        candidates=tuple(candidates[:limit]),
    )


def render_markdown(report: Report) -> str:
    lines = [
        "# 补丁债务候选",
        "",
        f"时间窗：{report.since} 至 {report.until}（{report.days} 天）",
        "",
        (
            f"共 {report.commits} 个提交，其中 {report.fix_commits} 个 fix；"
            f"{report.production_fix_commits} 个 fix 触及生产文件，"
            f"发现 {report.candidate_files} 个候选文件。"
        ),
        "",
        "分数 = 修复次数 × 2 + 7 日最大聚集 × 3 + 近 14 日修复次数 × 2。",
        "",
        "> 这里只列出历史修复热点，不代表当前仍有缺陷。确认债务前必须核对当前源码和测试。",
    ]
    if not report.candidates:
        lines.extend(["", "当前时间窗内没有达到阈值的候选。"])
        return "\n".join(lines) + "\n"

    lines.extend([
        "",
        "| 排名 | 生产文件 | 分数 | Fix/变更 | 7 日聚集 | 近 14 日 Fix | 最近修复 |",
        "| ---: | --- | ---: | ---: | ---: | ---: | --- |",
    ])
    for rank, candidate in enumerate(report.candidates, 1):
        path = candidate.path.replace("|", r"\|")
        lines.append(
            f"| {rank} | `{path}` | {candidate.score} | "
            f"{candidate.fixes}/{candidate.changes} | {candidate.burst_fixes} | "
            f"{candidate.recent_fixes} | {candidate.last_fix[:10]} |"
        )

    lines.extend(["", "## 最近证据"])
    for candidate in report.candidates:
        lines.extend(["", f"### `{candidate.path}`"])
        for evidence in candidate.evidence:
            subject = evidence.subject.replace("\n", " ").replace("|", r"\|")
            lines.append(f"- `{evidence.identity}` {subject}")
    return "\n".join(lines) + "\n"


def render_json(report: Report) -> str:
    payload = {
        "since": report.since,
        "until": report.until,
        "days": report.days,
        "commits": report.commits,
        "fix_commits": report.fix_commits,
        "production_fix_commits": report.production_fix_commits,
        "candidate_files": report.candidate_files,
        "candidates": [
            {
                "path": candidate.path,
                "score": candidate.score,
                "fixes": candidate.fixes,
                "changes": candidate.changes,
                "burst_fixes": candidate.burst_fixes,
                "recent_fixes": candidate.recent_fixes,
                "last_fix": candidate.last_fix,
                "evidence": [
                    {
                        "commit": evidence.identity,
                        "timestamp": evidence.timestamp,
                        "subject": evidence.subject,
                    }
                    for evidence in candidate.evidence
                ],
            }
            for candidate in report.candidates
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("必须是正整数")
    return number


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=positive_int, default=90)
    parser.add_argument("--limit", type=positive_int, default=20)
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--repo", type=Path, default=ROOT, help="Git 仓库根目录")
    args = parser.parse_args(argv)

    now = datetime.now(timezone.utc)
    since = now - timedelta(days=args.days)
    commits = read_commits(args.repo.resolve(), since=since)
    report = build_report(
        commits,
        days=args.days,
        limit=args.limit,
        now=now,
        current_files=read_current_files(args.repo.resolve()),
    )
    output = render_json(report) if args.format == "json" else render_markdown(report)
    sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
