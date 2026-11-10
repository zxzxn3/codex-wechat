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

python_version_hint() {
    if command -v apt-get >/dev/null 2>&1; then
        echo '注意：Ubuntu 22.04 等旧发行版的官方 python3 低于 3.11；仅重复安装 python3 不会满足要求。' >&2
    fi
}

missing_python() {
    echo '需要 Python 3.11+。可使用当前系统的包管理器安装：' >&2
    dependency_install_hint >&2
    python_version_hint
    echo '安装后重开终端并重跑。' >&2
}

find_python() {
    local candidate
    for candidate in python3 python3.13 python3.12 python3.11; do
        if command -v "$candidate" >/dev/null 2>&1 &&
                "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))' \
                    >/dev/null 2>&1; then
            command -v "$candidate"
            return 0
        fi
    done
    return 1
}

python_command=$(find_python) || { missing_python; exit 1; }
exec "$python_command" -u "$PROJECT_DIR/installer.py" "$@"
