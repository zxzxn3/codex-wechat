#!/usr/bin/env bash
# Local adapter for the same uv-based installer used by the network entry.
set -euo pipefail
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$PROJECT_DIR/bootstrap.sh" --source-dir "$PROJECT_DIR" "$@"
