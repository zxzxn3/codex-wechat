#!/usr/bin/env bash
# Local development checks. Install the Python tools with:
#   .venv/bin/python -m pip install -r requirements-dev.txt
set -euo pipefail
cd "$(dirname "$0")/.."

audit=0
case "${1:-}" in
    "") ;;
    --audit) audit=1 ;;
    *) echo "用法: $0 [--audit]" >&2; exit 2 ;;
esac

for tool in ruff pyright shellcheck; do
    command -v "$tool" >/dev/null 2>&1 || {
        echo "缺少 $tool；请先安装后重试。" >&2
        exit 1
    }
done

ruff check .
pyright --level warning

mapfile -d '' -t shell_files < <(
    find . -path './.venv' -prune -o -path './.state' -prune \
        -o -path './workspace' -prune -o -path './releases' -prune \
        -o -name '*.sh' -print0
)
if ((${#shell_files[@]})); then
    shellcheck -x "${shell_files[@]}"
fi

if ! .venv/bin/python -c 'import coverage' >/dev/null 2>&1; then
    echo "缺少 coverage；请先运行 .venv/bin/python -m pip install -r requirements-dev.txt" >&2
    exit 1
fi

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m coverage erase
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m coverage run -m unittest discover -s tests
.venv/bin/python -m coverage report --sort=cover

if ((audit)); then
    if ! .venv/bin/python -c 'import pip_audit' >/dev/null 2>&1; then
        echo "缺少 pip-audit；请先运行 .venv/bin/python -m pip install -r requirements-dev.txt" >&2
        exit 1
    fi
    .venv/bin/python -m pip_audit -r requirements.txt -r requirements-qr.txt --progress-spinner off
fi
