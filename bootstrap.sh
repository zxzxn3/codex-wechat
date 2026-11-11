#!/usr/bin/env bash
# Public entry and local uv adapter for installer.py.
set -euo pipefail

UV_VERSION=0.13.7
UV_PYTHON=3.11
repo='https://github.com/zxzxn3/codex-wechat'
source_ref=main
dry_run=0
non_interactive=0
target=''
source_dir=''
command_name=''

usage() {
    echo 'bootstrap.sh [--repo URL] [--ref main] [--dry-run] [--non-interactive]'
}

while (($#)); do
    case "$1" in
        --repo|--ref|--dir|--source-dir)
            (($# >= 2)) || { echo "缺少 $1 的参数" >&2; exit 2; }
            case "$1" in
                --repo) repo=$2 ;;
                --ref) source_ref=$2 ;;
                --dir) target=$2 ;;
                --source-dir) source_dir=$2 ;;
            esac
            shift 2 ;;
        --dry-run) dry_run=1; shift ;;
        --non-interactive) non_interactive=1; shift ;;
        install|update|menu) command_name=$1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "未知参数 $1" >&2; exit 2 ;;
    esac
done

if [[ -z "$source_dir" ]]; then
    if [[ ! "$repo" =~ ^https://github\.com/([A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_][A-Za-z0-9_.-]*)(\.git)?$ ]]; then
        echo '需要公开 GitHub HTTPS 仓库地址。' >&2
        exit 1
    fi
    repository=${BASH_REMATCH[1]}
    if [[ -z "$source_ref" || "$source_ref" == -* || "$source_ref" =~ [[:space:][:cntrl:]] ]]; then
        echo '需要有效的分支或标签名称。' >&2
        exit 1
    fi
fi

if ((dry_run)) && [[ -z "$source_dir" ]]; then
    printf '演练模式，不下载或修改文件。\n来源 https://github.com/%s\n分支或标签 %s\n' \
        "$repository" "$source_ref"
    echo '因 --dry-run，真实安装目录检查按通过处理；不代表安装环境已通过检查。'
    echo '1. 安装 / 更新  0. 退出'
    echo '正式执行会显示统一菜单，识别安装或更新，展示计划后执行；不扫码、不启用自启、不自动启动。'
    exit 0
fi

command -v curl >/dev/null || {
    echo '需要 curl。' >&2
    exit 1
}

task_temporary=$(mktemp -d -t codex-wechat-bootstrap.XXXXXXXX)
trap 'rm -rf -- "$task_temporary"' EXIT

uv_command=''
if command -v uv >/dev/null 2>&1; then
    uv_version_output=$(uv --version 2>/dev/null || true)
    if [[ "$uv_version_output" == "uv $UV_VERSION" ||
            "$uv_version_output" == "uv $UV_VERSION "* ]]; then
        uv_command=$(command -v uv)
    fi
fi
if [[ -z "$uv_command" ]]; then
    uv_root="$task_temporary/uv"
    curl --proto '=https' --tlsv1.2 --fail --silent --show-error --location \
        --connect-timeout 15 --max-time 120 \
        "https://astral.sh/uv/$UV_VERSION/install.sh" -o "$task_temporary/uv-install.sh"
    env UV_UNMANAGED_INSTALL="$uv_root" sh "$task_temporary/uv-install.sh" >/dev/null
    uv_command="$uv_root/uv"
fi
[[ -x "$uv_command" ]] || { echo "uv 安装失败: $uv_command" >&2; exit 1; }

export UV_CACHE_DIR="$task_temporary/cache"
export UV_MANAGED_PYTHON=1
export UV_NO_CONFIG=1
export UV_NO_MODIFY_PATH=1
export UV_NO_PROGRESS=1
export UV_PYTHON="$UV_PYTHON"
export UV_PYTHON_INSTALL_BIN=0
export UV_PYTHON_INSTALL_DIR="$task_temporary/python"
export CODEX_WECHAT_BOOTSTRAP_UV="$uv_command"

"$uv_command" python install "$UV_PYTHON"
python_command=$("$uv_command" python find --managed-python "$UV_PYTHON")
[[ -x "$python_command" ]] || { echo 'uv 管理的 Python 3.11 不可用。' >&2; exit 1; }

if [[ -n "$source_dir" ]]; then
    installer="$source_dir/installer.py"
    installer_arguments=("${command_name:-menu}")
    installer_arguments+=(--source "$source_dir")
else
    installer_base_url="https://raw.githubusercontent.com/$repository/$source_ref"
    curl --proto '=https' --tlsv1.2 --fail --silent --show-error --location \
        --connect-timeout 15 --max-time 120 "$installer_base_url/installer.py" \
        -o "$task_temporary/installer.py"
    installer="$task_temporary/installer.py"
    installer_arguments=(bootstrap --repo "$repo" --ref "$source_ref")
fi
[[ -f "$installer" ]] || { echo "安装器不存在: $installer" >&2; exit 1; }

[[ -z "$target" ]] || installer_arguments+=(--dir "$target")
if ((dry_run)); then
    installer_arguments+=(--dry-run)
fi
if ((non_interactive)); then
    installer_arguments+=(--non-interactive)
fi

exec "$python_command" -u "$installer" "${installer_arguments[@]}"
