#!/usr/bin/env bash
# Install only the CLI link; services are created by the autostart setting.
set -euo pipefail
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
EXPECTED_DIR="$HOME/.local/share/codex-wechat"
BIN_DIR="$HOME/.local/bin"
UNIT=codex-wechat.service
die() { echo "install.sh: $*" >&2; exit 1; }
[ "$#" -eq 0 ] || die '用法：install.sh；自启请使用 /settings autostart on|off'
[ "$PROJECT_DIR" = "$EXPECTED_DIR" ] || die "项目须放在 $EXPECTED_DIR"
WARNINGS=()
warn() { WARNINGS+=("$*"); echo "提示: $*" >&2; }
PYTHON=python3
if [ -x "$PROJECT_DIR/.venv/bin/python" ]; then
    PYTHON="$PROJECT_DIR/.venv/bin/python"
fi
if ! command -v "$PYTHON" >/dev/null ||
        ! "$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
    warn '需要 Python 3.11+；命令链接仍会安装，运行、扫码和自启设置暂不可用。'
else
    "$PYTHON" -c 'from cryptography.hazmat.primitives.ciphers import algorithms; algorithms.AES128' 2>/dev/null ||
        warn '缺少 cryptography 38+；附件加解密暂不可用。'
    "$PYTHON" -c 'from websockets.asyncio.client import unix_connect' 2>/dev/null ||
        warn '缺少 websockets 15–17；共享 Codex 后台暂不可用。'
fi
if ! command -v curl >/dev/null; then
    warn '缺少 curl 8.4+；微信登录和收发暂不可用。'
else
    curl_version=$(curl --version 2>/dev/null || true)
    if [[ ! "$curl_version" =~ ^curl[[:space:]]+([0-9]+)\.([0-9]+) ]] ||
            ! (( 10#${BASH_REMATCH[1]:-0} > 8 ||
                 (10#${BASH_REMATCH[1]:-0} == 8 && 10#${BASH_REMATCH[2]:-0} >= 4) )); then
        warn 'curl 版本不足 8.4 或无法检查；微信收发暂不可用。'
    fi
fi
if ! command -v codex >/dev/null && [ ! -x "$HOME/.local/bin/codex" ]; then
    warn '未发现 Codex CLI；只安装桌面 App 不代表已有 app-server 命令。'
fi
command -v qrencode >/dev/null || warn '缺少 qrencode；登录时无法在终端绘制二维码，仍会显示链接。'
if ! command -v systemctl >/dev/null ||
        ! systemctl --user show --property=Version --value >/dev/null 2>&1; then
    warn '用户 systemd 不可用；以后开启自启时无法注册服务，命令链接仍会安装。'
fi
if ! command -v systemd-run >/dev/null; then
    warn '缺少 systemd-run；无法自动启动共享 Codex 后台。'
else
    SYSTEMD_VERSION=$(systemd-run --version 2>/dev/null || true)
    SYSTEMD_VERSION=${SYSTEMD_VERSION%%$'\n'*}
    SYSTEMD_VERSION=${SYSTEMD_VERSION#systemd }
    SYSTEMD_VERSION=${SYSTEMD_VERSION%% *}
    if [[ ! "$SYSTEMD_VERSION" =~ ^[0-9]+$ ]] || [ "$SYSTEMD_VERSION" -lt 254 ]; then
        warn 'systemd-run 需要 254+ 才能自动启动共享 Codex 后台。'
    fi
fi
[ -x "$PROJECT_DIR/codex-wechat" ] || die 'codex-wechat 入口不可执行'
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
check_link "$PROJECT_DIR/codex-wechat" "$BIN_DIR/codex-wechat"
mkdir -p -- "$BIN_DIR"
if [ ! -L "$BIN_DIR/codex-wechat" ]; then
    ln -s -- "$(realpath --relative-to="$BIN_DIR" -- "$PROJECT_DIR/codex-wechat")" "$BIN_DIR/codex-wechat"
fi
echo '命令入口已安装；本安装器不注册服务、不修改现有自启，也不启动程序。'
echo '新安装默认不自启。先执行 codex-wechat login 和 codex-wechat start，之后可在微信使用 /settings autostart on。'
echo '重新安装不会重置已有服务的自启状态。'
if [ "${#WARNINGS[@]}" -gt 0 ]; then
    echo "共 ${#WARNINGS[@]} 条依赖提示；补齐后可重新运行安装器检查。"
fi
