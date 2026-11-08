#!/usr/bin/env python3
"""Render a static development-activity chart from this repository's git history.

The repository is private during beta, so external chart services cannot read
it. This script writes a self-contained SVG for the README instead.
"""
from __future__ import annotations

import subprocess
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "assets" / "dev-activity.svg"
WIDTH, HEIGHT = 1100, 520


def git_stats() -> dict[date, dict[str, int]]:
    result = subprocess.run(
        ["git", "log", "--numstat", "--format=COMMIT %ad", "--date=short"],
        cwd=ROOT, check=True, capture_output=True, text=True,
    )
    stats: dict[date, dict[str, int]] = defaultdict(
        lambda: {"commits": 0, "additions": 0, "deletions": 0})
    current: date | None = None
    for line in result.stdout.splitlines():
        if line.startswith("COMMIT "):
            current = date.fromisoformat(line[7:])
            stats[current]["commits"] += 1
            continue
        if current is None or not line:
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        try:
            stats[current]["additions"] += int(parts[0])
        except ValueError:
            pass
        try:
            stats[current]["deletions"] += int(parts[1])
        except ValueError:
            pass
    return dict(stats)


def fill_days(stats: dict[date, dict[str, int]]) -> list[date]:
    if not stats:
        return []
    start, end = min(stats), max(stats)
    days = []
    cursor = start
    while cursor <= end:
        days.append(cursor)
        cursor += timedelta(days=1)
    return days


def bars(values: list[int], x: float, y: float, width: float, height: float,
         color: str, gap: float) -> str:
    peak = max(values) or 1
    slot = width / max(len(values), 1)
    body = []
    for index, value in enumerate(values):
        bar_height = (value / peak) * height
        left = x + index * slot + (slot - gap) / 2
        top = y + height - bar_height
        body.append(
            f'<rect x="{left:.1f}" y="{top:.1f}" width="{gap:.1f}" '
            f'height="{bar_height:.1f}" rx="6" fill="{color}"/>'
        )
        body.append(
            f'<text x="{left + gap / 2:.1f}" y="{top - 5:.1f}" '
            f'text-anchor="middle" class="value">{value:,}</text>'
        )
    return "".join(body)


def render() -> str:
    stats = git_stats()
    days = fill_days(stats)
    values = [stats.get(day, {"commits": 0, "additions": 0, "deletions": 0})
              for day in days]
    commits = [item["commits"] for item in values]
    additions = [item["additions"] for item in values]
    deletions = [item["deletions"] for item in values]
    total_commits = sum(commits)
    total_add = sum(additions)
    total_del = sum(deletions)
    first = days[0].isoformat() if days else "-"
    last = days[-1].isoformat() if days else "-"

    chart_x, chart_w = 78, 948
    commit_y, commit_h = 156, 92
    line_y, line_h = 344, 84
    slot = chart_w / max(len(days), 1)
    labels = "".join(
        f'<text x="{chart_x + index * slot + slot / 2:.1f}" y="{line_y + line_h + 28}" '
        f'text-anchor="middle" class="axis">{day.strftime("%m-%d")}</text>'
        for index, day in enumerate(days)
    )
    bars_commits = bars(commits, chart_x, commit_y, chart_w, commit_h, "#72c945", slot * .56)
    bars_add = bars(additions, chart_x, line_y, chart_w, line_h, "#3157ff", slot * .32)
    bars_del = []
    for index, value in enumerate(deletions):
        left = chart_x + index * slot + slot * .58
        height = (value / max(max(additions) or 1, max(deletions) or 1)) * line_h
        bars_del.append(
            f'<rect x="{left:.1f}" y="{line_y + line_h - height:.1f}" '
            f'width="{slot * .32:.1f}" height="{height:.1f}" rx="6" fill="#f36d49"/>'
        )
    grid = "".join(
        f'<line x1="{chart_x}" x2="{chart_x + chart_w}" y1="{commit_y + commit_h * i / 2:.1f}" '
        f'y2="{commit_y + commit_h * i / 2:.1f}" class="grid"/>'
        for i in range(3)
    )
    grid += "".join(
        f'<line x1="{chart_x}" x2="{chart_x + chart_w}" y1="{line_y + line_h * i / 2:.1f}" '
        f'y2="{line_y + line_h * i / 2:.1f}" class="grid"/>'
        for i in range(3)
    )

    def card(x: int, label: str, value: str, color: str) -> str:
        return (
            f'<rect x="{x}" y="42" width="190" height="66" rx="16" fill="#fffdf8" '
            f'stroke="#e3ddcf"/>'
            f'<text x="{x + 18}" y="66" class="card-label">{label}</text>'
            f'<text x="{x + 18}" y="94" class="card-value" fill="{color}">{value}</text>'
        )

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-labelledby="title desc">
<title id="title">codex-wechat 开发活动</title>
<desc id="desc">按日统计的提交数、代码新增和删除行数，数据来自 git 历史。</desc>
<style>
  text {{ font-family: "Noto Sans CJK SC", "Source Han Sans CN", "Avenir Next", sans-serif; }}
  .title {{ font-size: 27px; font-weight: 700; fill: #151713; }}
  .subtitle {{ font-size: 13px; fill: #6d7167; }}
  .section {{ font-size: 13px; font-weight: 700; fill: #2b2f27; }}
  .axis {{ font-size: 11px; fill: #6d7167; }}
  .value {{ font-size: 10px; fill: #2b2f27; }}
  .card-label {{ font-size: 11px; fill: #6d7167; }}
  .card-value {{ font-size: 24px; font-weight: 700; }}
  .grid {{ stroke: #e7e1d4; stroke-width: 1; }}
  .legend {{ font-size: 11px; fill: #6d7167; }}
  .foot {{ font-size: 11px; fill: #6d7167; }}
</style>
<rect width="{WIDTH}" height="{HEIGHT}" fill="#f7f2e7"/>
<rect x="24" y="20" width="{WIDTH - 48}" height="{HEIGHT - 40}" rx="24" fill="#fffdf8" stroke="#e7e1d4"/>
<text x="48" y="62" class="title">开发活动</text>
<text x="48" y="86" class="subtitle">codex-wechat · {first} – {last} · 来自本仓库 git 历史</text>
{card(520, "提交", f"{total_commits:,}", "#72c945")}
{card(720, "新增行", f"{total_add:,}", "#3157ff")}
{card(920, "删除行", f"{total_del:,}", "#f36d49")}
<text x="{chart_x}" y="{commit_y - 12}" class="section">每日提交</text>
{grid}{bars_commits}
<text x="{chart_x}" y="{line_y - 22}" class="section">每日代码变动</text>
<rect x="{chart_x + chart_w - 196}" y="{line_y - 46}" width="10" height="10" rx="3" fill="#3157ff"/>
<text x="{chart_x + chart_w - 180}" y="{line_y - 37}" class="legend">新增</text>
<rect x="{chart_x + chart_w - 116}" y="{line_y - 46}" width="10" height="10" rx="3" fill="#f36d49"/>
<text x="{chart_x + chart_w - 100}" y="{line_y - 37}" class="legend">删除</text>
{bars_add}{"".join(bars_del)}{labels}
<text x="48" y="{HEIGHT - 24}" class="foot">图表由 scripts/dev_chart.py 生成；含仓库初始导入，数字随提交历史变化。</text>
</svg>
'''


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(render(), encoding="utf-8")
    print(OUTPUT)


if __name__ == "__main__":
    main()
