#!/usr/bin/env bash
# Link the CLI and user unit, following ime-cursor's local installation convention.
set -euo pipefail
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
EXPECTED_DIR="$HOME/.local/share/agent-wechat"
BIN_DIR="$HOME/.local/bin"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT=agent-wechat.service
START=1
die() { echo "install.sh: $*" >&2; exit 1; }
case "${1:-}" in
    '') ;;
    --no-start) START=0 ;;
    *) die '用法：install.sh [--no-start]' ;;
esac
[ "$#" -le 1 ] || die '参数过多'
[ "$PROJECT_DIR" = "$EXPECTED_DIR" ] || die "项目须放在 $EXPECTED_DIR"
command -v python3 >/dev/null || die '缺少 python3'
PYTHON=python3
if [ -x "$PROJECT_DIR/.venv/bin/python" ]; then
    PYTHON="$PROJECT_DIR/.venv/bin/python"
fi
"$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 11))' || die '需要 Python 3.11+'
command -v curl >/dev/null || die '附件传输需要 curl'
"$PYTHON" -c 'from cryptography.hazmat.primitives.ciphers import algorithms; algorithms.AES128' 2>/dev/null ||
    die '附件功能需要 Python cryptography 38+（本机可用系统包 python-cryptography）'
"$PYTHON" -c 'from websockets.asyncio.client import unix_connect' 2>/dev/null ||
    die '共享后台需要 websockets 15–17，请按 README 安装项目依赖'
command -v systemctl >/dev/null || die '缺少 systemctl'
command -v systemd-run >/dev/null || die '共享后台自动启动需要 systemd-run'
SYSTEMD_VERSION=$(systemd-run --version)
SYSTEMD_VERSION=${SYSTEMD_VERSION%%$'\n'*}
SYSTEMD_VERSION=${SYSTEMD_VERSION#systemd }
SYSTEMD_VERSION=${SYSTEMD_VERSION%% *}
[[ "$SYSTEMD_VERSION" =~ ^[0-9]+$ ]] && [ "$SYSTEMD_VERSION" -ge 254 ] ||
    die '共享后台自动启动需要 systemd 254+'
[ -x "$PROJECT_DIR/agent-wechat" ] || die 'agent-wechat 入口不可执行'
[ -f "$PROJECT_DIR/$UNIT" ] || die "缺少 $UNIT"

# Preflight both destinations before changing either; unrelated files are never overwritten.
check_link() {
    local source=$1 target=$2
    if [ -L "$target" ]; then
        [ "$(readlink -f -- "$target")" = "$(readlink -f -- "$source")" ] ||
            die "$target 已指向别处，请先自行处理"
    elif [ -e "$target" ]; then
        die "$target 已存在且不是本项目的链接"
    fi
}
check_link "$PROJECT_DIR/agent-wechat" "$BIN_DIR/agent-wechat"
check_link "$PROJECT_DIR/$UNIT" "$UNIT_DIR/$UNIT"
mkdir -p -- "$BIN_DIR" "$UNIT_DIR"
if [ ! -L "$BIN_DIR/agent-wechat" ]; then
    ln -s -- "$(realpath --relative-to="$BIN_DIR" -- "$PROJECT_DIR/agent-wechat")" "$BIN_DIR/agent-wechat"
fi
if [ ! -L "$UNIT_DIR/$UNIT" ]; then
    ln -s -- "$PROJECT_DIR/$UNIT" "$UNIT_DIR/$UNIT"
fi
systemctl --user daemon-reload
systemctl --user enable "$UNIT"
if [ "$START" = 0 ]; then
    echo '已启用下次图形登录自启；未启动、停止或重启当前进程。'
elif [ ! -f "$PROJECT_DIR/.state/account.json" ]; then
    echo '尚未绑定微信，请先执行 agent-wechat login，然后 systemctl --user start agent-wechat。'
else
    # A foreground bridge keeps its connection; don't start a competing systemd instance.
    if "$PYTHON" - "$PROJECT_DIR/.state/lock" <<'PY'
import fcntl
from pathlib import Path
import sys
path = Path(sys.argv[1])
if path.exists():
    with path.open() as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            sys.exit(1)
PY
    then
        systemctl --user start "$UNIT"
        echo '已请求启动；应用健康情况请查看服务日志。'
    else
        echo '已有桥接占用状态目录，保留当前进程；退出前台桥接后可手动启动服务。'
    fi
fi
echo "journalctl --user -u $UNIT -f  # 查看日志"
