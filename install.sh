#!/usr/bin/env bash
# Local adapter for the same interactive installer used by the network entry.
set -euo pipefail
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

dependency_install_hint() {
    if command -v pacman >/dev/null 2>&1; then
        echo '  sudo pacman -S --needed python git curl'
    elif command -v apt-get >/dev/null 2>&1; then
        echo '  sudo apt-get install -y python3 git curl'
    elif command -v dnf >/dev/null 2>&1; then
        echo '  sudo dnf install -y python3 git curl'
    elif command -v zypper >/dev/null 2>&1; then
        echo '  sudo zypper install python3 git curl'
    elif command -v apk >/dev/null 2>&1; then
        echo '  sudo apk add python3 git curl'
    else
        echo '  Python: https://www.python.org/downloads/'
        echo '  Git: https://git-scm.com/downloads'
    fi
}

missing_python() {
    echo '需要 Python 3.11+。可使用当前系统的包管理器安装：' >&2
    dependency_install_hint >&2
    echo '安装后重开终端并重跑。' >&2
}

command -v python3 >/dev/null || { missing_python; exit 1; }
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' || { missing_python; exit 1; }
exec python3 -u "$PROJECT_DIR/installer.py" "$@"
