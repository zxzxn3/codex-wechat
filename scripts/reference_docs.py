#!/usr/bin/env python3
"""Render the code-owned command, setting, and delivery-guard reference."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from codex_wechat.command_specs import COMMANDS  # noqa: E402
from codex_wechat.commands import SETTINGS  # noqa: E402
from codex_wechat.output_delivery import (  # noqa: E402
    DELIVERY_DEGRADED_THRESHOLD,
    SEND_SPACING_SECONDS,
)

TARGET = ROOT / "docs" / "REFERENCE.generated.md"
PUBLIC_NUMERIC_SETTINGS = (
    "connectionIdleSeconds",
    "media.maxMiB",
    "slicing.targetChars",
    "logs.keep",
    "sending.replyBudget",
)


def render() -> str:
    lines = [
        "# 命令与设置参考",
        "",
        "> 本文件由 `scripts/reference_docs.py` 从命令目录和设置边界生成。",
        "> 修改实现后运行该脚本更新；`scripts/check.sh` 会用 `--check` 检查漂移。",
        "",
        "## 微信命令短别名",
        "",
        "| 短名 | 完整命令 |",
        "| --- | --- |",
    ]
    for spec in COMMANDS:
        for alias in spec.aliases:
            lines.append(f"| `{alias}` | `{spec.name}` |")

    lines.extend([
        "",
        "## 全部微信命令",
        "",
        "| 命令 | 用途 |",
        "| --- | --- |",
    ])
    for spec in COMMANDS:
        description = spec.description.replace("|", r"\|").replace("\n", " ")
        lines.append(f"| `{spec.name}` | {description} |")

    lines.extend([
        "",
        "## 数值设置",
        "",
        "| 设置 | 默认值 | 范围 |",
        "| --- | ---: | --- |",
    ])
    for name in PUBLIC_NUMERIC_SETTINGS:
        setting = SETTINGS[name]
        lines.append(
            f"| `{name}` | {setting.default} | {setting.minimum}–{setting.maximum} |"
        )

    lines.extend([
        "",
        "## 出站保护",
        "",
        "| 保护 | 当前值 |",
        "| --- | ---: |",
        f"| 连续出站回复间隔 | {SEND_SPACING_SECONDS:g}s |",
        f"| `delivery=unconfirmed` 的连续失败阈值 | {DELIVERY_DEGRADED_THRESHOLD} |",
        "",
        "> 命令来自 `codex_wechat.command_specs`；设置由 `codex_wechat.commands.SETTINGS`",
        "> 汇总，默认值和范围常量由 `codex_wechat.configuration`、",
        "> `codex_wechat.media` 和 `codex_wechat.log_rotation` 定义；出站保护值来自",
        "> `codex_wechat.output_delivery`。不在手写文档中维护副本。",
    ])
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args not in ([], ["--check"]):
        print("用法: scripts/reference_docs.py [--check]", file=sys.stderr)
        return 2
    expected = render()
    if args == ["--check"]:
        actual = TARGET.read_text(encoding="utf-8") if TARGET.is_file() else ""
        if actual != expected:
            print(
                "docs/REFERENCE.generated.md 已过期；"
                "运行 `.venv/bin/python scripts/reference_docs.py`",
                file=sys.stderr,
            )
            return 1
        print("generated reference: up to date")
        return 0
    TARGET.write_text(expected, encoding="utf-8")
    print(f"wrote {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
