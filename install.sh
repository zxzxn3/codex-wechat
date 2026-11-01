#!/usr/bin/env bash
# Local adapter for the same interactive installer used by the network entry.
set -euo pipefail
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
command -v python3 >/dev/null || { echo '需要 Python 3.11+，请安装后重跑。' >&2; exit 1; }
exec python3 -u "$PROJECT_DIR/installer.py" "$@"
