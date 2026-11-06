#!/usr/bin/env bash
# One-line entry. A private repository is authenticated with CODEX_WECHAT_TOKEN.
set -euo pipefail

main() {
    local repo='https://github.com/zxzxn3/codex-wechat' source_ref=main dry_run=0 non_interactive=0 target='' component='' skills_dir='' python_command installer_url release_url task_temporary auth private_source=0 installer
    while (($#)); do
        case "$1" in
            --repo|--ref|--dir|--component|--skills-dir)
                (($# >= 2)) || { echo "缺少 $1 的参数" >&2; return 2; }
                case "$1" in
                    --repo) repo=$2 ;;
                    --ref) source_ref=$2 ;;
                    --dir) target=$2 ;;
                    --component) component=$2 ;;
                    --skills-dir) skills_dir=$2 ;;
                esac
                shift 2 ;;
            --dry-run) dry_run=1; shift ;;
            --non-interactive) non_interactive=1; shift ;;
            -h|--help)
                echo 'bootstrap.sh [--repo URL] [--ref main] [--dry-run] [--non-interactive] [--component NAME] [--skills-dir PATH]'
                return 0 ;;
            *) echo "未知参数 $1" >&2; return 2 ;;
        esac
    done
    python_command=$(command -v python3) || { echo '需要 Python 3.11+，安装后重跑相同命令。' >&2; return 1; }
    "$python_command" -c 'import sys; sys.exit(sys.version_info < (3, 11))' || {
        echo '需要 Python 3.11+，安装后重跑相同命令。' >&2; return 1;
    }
    installer_base_url=$("$python_command" -c '
import re, sys
from urllib.parse import quote, urlsplit
url = urlsplit(sys.argv[1]); path = url.path.strip("/").removesuffix(".git")
ref = sys.argv[2]
if (url.scheme != "https" or url.netloc.lower() != "github.com" or url.query or url.fragment
    or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_][A-Za-z0-9_.-]*", path)
    or not ref or ref.startswith("-") or any(c.isspace() or ord(c) < 32 for c in ref)):
    sys.exit("需要 GitHub HTTPS 仓库地址和分支或标签名称。")
print("https://raw.githubusercontent.com/" + path + "/" + quote(ref, safe=""))
' "$repo" "$source_ref") || return 1
    installer_url="$installer_base_url/installer.py"
    release_url="$installer_base_url/release.py"
    if ((dry_run)); then
        printf '演练模式，不下载或修改文件。\n来源 %s\n分支或标签 %s\n' "$repo" "$source_ref"
        if [[ -n "${CODEX_WECHAT_TOKEN:-}" ]]; then
            echo '将使用 CODEX_WECHAT_TOKEN 认证私有仓库。'
        fi
        echo '因 --dry-run，真实安装目录检查按通过处理；不代表安装环境已通过检查。'
        echo '1. 安装 / 更新  2. 附加组件  0. 退出'
        echo '正式执行会显示统一菜单，识别安装或更新，展示计划后执行；不扫码、不启用自启、不自动启动。'
        return 0
    fi
    command -v git >/dev/null || { echo '需要 Git，安装后重跑相同命令。' >&2; return 1; }
    # curl | bash consumes stdin. Confirmation must read the real terminal.
    if ((!non_interactive)) && ! { exec 3<> /dev/tty; } 2>/dev/null; then
        echo '安装确认需要交互终端；演练可以使用 --dry-run。' >&2
        return 1
    fi
    task_temporary=$(mktemp -d -t codex-wechat-bootstrap.XXXXXXXX)
    # Freeze the safely quoted path now because task_temporary is local to main().
    # shellcheck disable=SC2064
    trap "$(printf 'rm -rf -- %q' "$task_temporary")" EXIT
    if [[ -n "${CODEX_WECHAT_TOKEN:-}" ]]; then
        private_source=1
        auth=$("$python_command" -c '
import base64, os, re, sys
token = os.environ.get("CODEX_WECHAT_TOKEN", "")
if not re.fullmatch(r"[A-Za-z0-9_]+", token):
    sys.exit("CODEX_WECHAT_TOKEN 格式无效")
print("Authorization: Basic " + base64.b64encode(
    ("x-access-token:" + token).encode()).decode())
' 2>/dev/null) || { echo 'CODEX_WECHAT_TOKEN 格式无效。' >&2; return 1; }
        # Keep the credential out of the remote URL and the installer's environment.
        if ! (
            unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_OBJECT_DIRECTORY
            unset GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_COMMON_DIR GIT_NAMESPACE
            GIT_TERMINAL_PROMPT=0 git -c credential.helper= -c core.hooksPath=/dev/null \
                -c http.extraHeader="$auth" clone --depth 1 --single-branch \
                --branch "$source_ref" -- "$repo" "$task_temporary/source"
        ); then
            echo '私有仓库下载失败；请检查 PAT 是否仍有效并具有该仓库的 Contents: Read-only 权限。' >&2
            return 1
        fi
        unset auth CODEX_WECHAT_TOKEN
        installer="$task_temporary/source/installer.py"
        local -a installer_arguments=("$installer" menu)
    else
        command -v curl >/dev/null || { echo '需要 curl，安装后重跑相同命令。' >&2; return 1; }
        curl --proto '=https' --tlsv1.2 --fail --silent --show-error --location \
            --connect-timeout 15 --max-time 120 "$installer_url" -o "$task_temporary/installer.py"
        curl --proto '=https' --tlsv1.2 --fail --silent --show-error --location \
            --connect-timeout 15 --max-time 120 "$release_url" -o "$task_temporary/release.py"
        installer="$task_temporary/installer.py"
        local -a installer_arguments=("$installer" bootstrap --repo "$repo" --ref "$source_ref")
    fi
    [[ -z "$target" ]] || installer_arguments+=(--dir "$target")
    if [[ -n "$component" ]]; then
        installer_arguments[1]=components
        installer_arguments+=(--component "$component")
        if ((!private_source)); then
            installer_arguments+=(--repo "$repo" --ref "$source_ref")
        fi
    fi
    [[ -z "$skills_dir" ]] || installer_arguments+=(--skills-dir "$skills_dir")
    if ((non_interactive)); then
        installer_arguments+=(--non-interactive)
        "$python_command" -u "${installer_arguments[@]}"
    else
        "$python_command" -u "${installer_arguments[@]}" <&3 >&3
    fi
}

# Parse the complete entry before invoking native programs or reading /dev/tty.
main "$@"
