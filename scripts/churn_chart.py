#!/usr/bin/env python3
"""Render the README development activity chart from Git history."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "assets" / "code-churn-12h-split.svg"

BUCKET = timedelta(hours=12)
WINDOW_BUCKETS = 17
IDLE_GAP = timedelta(hours=2)
SHANGHAI = timezone(timedelta(hours=8))

PLOT_LEFT = 24.0
PLOT_RIGHT = 696.0
BASELINE_Y = 130.0
MAX_BAR_HEIGHT = 108.34
BAR_WIDTH = 18.0
MIN_BAR_HEIGHT = 2.5

EXCLUDED_PREFIXES = ("assets/code-churn-",)


@dataclass(frozen=True)
class Commit:
    timestamp: datetime
    added: int
    deleted: int


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), *args],
        text=True,
        encoding="utf-8",
    )


def parse_log(text: str) -> list[Commit]:
    commits: list[Commit] = []
    timestamp = None
    added = 0
    deleted = 0
    meaningful = False

    def flush() -> None:
        nonlocal timestamp, added, deleted, meaningful
        if timestamp is not None and meaningful:
            commits.append(Commit(timestamp, added, deleted))
        timestamp = None
        added = 0
        deleted = 0
        meaningful = False

    for line in text.splitlines():
        if line.startswith("__COMMIT__\t"):
            flush()
            _, raw_timestamp = line.split("\t", 1)
            timestamp = datetime.fromisoformat(raw_timestamp).astimezone(SHANGHAI)
            continue
        if timestamp is None:
            continue
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        path = parts[2]
        if path.startswith(EXCLUDED_PREFIXES):
            continue
        meaningful = True
        try:
            added += int(parts[0])
            deleted += int(parts[1])
        except ValueError:
            continue
    flush()
    return commits


def read_commits(repo: Path) -> list[Commit]:
    text = _git(
        repo,
        "log",
        "--reverse",
        "--format=__COMMIT__%x09%aI",
        "--numstat",
    )
    return parse_log(text)


def _bucket_start(moment: datetime) -> datetime:
    hour = 0 if moment.hour < 12 else 12
    return moment.replace(hour=hour, minute=0, second=0, microsecond=0)


def bucket_series(
    commits: list[Commit],
    count: int = WINDOW_BUCKETS,
) -> tuple[datetime, datetime, list[tuple[int, int]]]:
    if not commits:
        raise ValueError("Git history has no chartable commits")
    end = _bucket_start(max(commit.timestamp for commit in commits))
    start = end - BUCKET * (count - 1)
    buckets = [(0, 0) for _ in range(count)]
    for commit in commits:
        index = (_bucket_start(commit.timestamp) - start) // BUCKET
        if 0 <= index < count:
            added, deleted = buckets[index]
            buckets[index] = (added + commit.added, deleted + commit.deleted)
    return start, end, buckets


def estimate_hours(commits: list[Commit]) -> float:
    timestamps = sorted(commit.timestamp for commit in commits)
    if not timestamps:
        return 0.0
    total = 0.0
    start = previous = timestamps[0]
    for timestamp in timestamps[1:]:
        if timestamp - previous > IDLE_GAP:
            total += (previous - start).total_seconds()
            start = timestamp
        previous = timestamp
    total += (previous - start).total_seconds()
    return total / 3600


def python_line_counts(repo: Path) -> tuple[int, int]:
    raw_paths = subprocess.check_output(
        [
            "git",
            "-C",
            str(repo),
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
            "--",
            "*.py",
        ],
    )
    source = 0
    tests = 0
    for raw_path in raw_paths.split(b"\0"):
        if not raw_path:
            continue
        relative = raw_path.decode("utf-8")
        try:
            text = (repo / relative).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        count = len(text.splitlines())
        if relative.startswith("tests/"):
            tests += count
        else:
            source += count
    return source, tests


def format_count(value: int) -> str:
    if value < 1000:
        return str(value)
    return f"{value / 1000:.1f}K"


def render_svg(commits: list[Commit], source_lines: int, test_lines: int) -> str:
    start, end, buckets = bucket_series(commits)
    maximum = max((max(added, deleted) for added, deleted in buckets), default=0)
    scale = MAX_BAR_HEIGHT / maximum if maximum else 0
    step = (PLOT_RIGHT - PLOT_LEFT) / WINDOW_BUCKETS
    bars = []
    for index, (added, deleted) in enumerate(buckets):
        center = PLOT_LEFT + (index + 0.5) * step
        x = center - BAR_WIDTH / 2
        if added:
            height = max(added * scale, MIN_BAR_HEIGHT)
            bars.append(
                f'<rect x="{x:.2f}" y="{BASELINE_Y - height:.2f}" '
                f'width="{BAR_WIDTH:g}" height="{height:.2f}" fill="#3fb950"/>'
            )
        if deleted:
            height = max(deleted * scale, MIN_BAR_HEIGHT)
            bars.append(
                f'<rect x="{x:.2f}" y="{BASELINE_Y:.2f}" '
                f'width="{BAR_WIDTH:g}" height="{height:.2f}" fill="#f85149"/>'
            )

    hours = estimate_hours(commits)
    stats = (
        f"{format_count(source_lines)} source · "
        f"{format_count(test_lines)} tests · ~{hours:.0f}h dev"
    )
    description = (
        "绿色柱为新增行，红色柱为删除行；每 12 小时聚合，"
        f"从 {start:%Y-%m-%d %H:%M} 到 {end:%Y-%m-%d %H:%M}。"
    )
    return "\n".join([
        '<svg xmlns="http://www.w3.org/2000/svg" width="1440" height="360" '
        'viewBox="0 16 720 180" role="img" aria-labelledby="title desc">',
        '<title id="title">每 12 小时代码增加与删除</title>',
        f'<desc id="desc">{escape(description)}</desc>',
        '<rect x="0" y="16" width="720" height="180" fill="#0d1117"/>',
        '<text x="696" y="186" text-anchor="end" fill="#8b949e" '
        'font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif" '
        f'font-size="12" font-weight="500">{escape(stats)}</text>',
        "".join(bars),
        '<line x1="24.00" y1="130.00" x2="696.00" y2="130.00" '
        'stroke="#30363d" stroke-width="2" stroke-linecap="round"/>',
        "</svg>",
        "",
    ])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=TARGET)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    commits = read_commits(ROOT)
    source_lines, test_lines = python_line_counts(ROOT)
    expected = render_svg(commits, source_lines, test_lines)
    if args.check:
        actual = args.output.read_text(encoding="utf-8") if args.output.is_file() else ""
        if actual != expected:
            print(
                f"{args.output} 已过期；运行 scripts/churn_chart.py 更新",
                file=sys.stderr,
            )
            return 1
        print("activity chart: up to date")
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(expected, encoding="utf-8")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
