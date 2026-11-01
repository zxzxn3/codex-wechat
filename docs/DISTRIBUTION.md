# 双平台发布方向

主分支使用同一套 Linux / Windows CLI 桥接源码。Windows 已有部分安装实机证据，但不能据此认定全部在线路径通过；尚未发布独立 exe 或 GitHub Release，公开项目仓库地址待定。

一行安装入口为 `bootstrap.sh` / `bootstrap.ps1`，通过显式仓库参数获取安装器，自动识别安装或更新。第一次和以后更新重跑相同入口即可，具体项目安装逻辑继续集中在 installer.py。当前入口依赖已有 Python 3.11+ 和 Git，不等于免环境的二进制安装包；入口不自动修改系统依赖，也不在安装时扫码。发布 URL 未确定前，文档保留明确占位符，不引用已退役的私人库。

源码安装和更新由标准库实现的 `installer.py` 负责：Linux 默认部署到
`~/.local/share/codex-wechat`，Windows 默认部署到
`%LOCALAPPDATA%\codex-wechat\app`。程序按内容指纹安装到不可变
`releases/<版本>`，每个版本拥有独立 `.venv`；根目录 `current` 原子指向
当前版本，`previous` 支持只切指针回滚。稳定命令入口和 Windows bootstrap
supervisor 不随普通更新改写。平台服务仍由现有 systemd unit 或 Windows
计划任务脚本管理。安装器预留
`assets/logo/codex-wechat.txt` 作为终端 art 接口，当前不附带 art。

主要参考 [yt-dlp 的发行文件和构建说明](https://github.com/yt-dlp/yt-dlp#release-files)：同一仓库和版本，分别产出平台适配的文件。自启接入参考 [Syncthing](https://docs.syncthing.net/users/autostart)：业务核心共享，接入各系统原生启动机制，不要求跨平台共用同一服务管理器。

下一阶段先添加 Windows/Linux CI，执行各自的离线用例及安装入口检查；再从同一 tag 用平台构建任务生成 Windows x64 / Linux x64 产物及校验和。优先打包目录而非单文件，便于随包带安装脚本、技能和数据文件；不复制用户登录、密钥或状态。具体构建工具尚未实现。

Python 工具可评估 PyInstaller，它可以带上 Python 与依赖，让使用者不用另装 Python，但[官方文档](https://pyinstaller.org/en/stable/operating-mode.html)要求在目标 OS 构建，不能把 Linux 本地通过等同于 Windows 包可用。现有依赖外部 Python 启动 supervisor/桥接的入口需先适配冻结程序，不能只改扩展名就发布。Codex CLI 的安装、登录、版本兼容，以及 curl 的供应仍需单独处理，不把它们打包后的可用性当成既成事实。
