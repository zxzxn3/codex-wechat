# 双平台发布方向

当前 cross-platform 分支是一套源码，Windows 尚未完成实机验收。本轮实现登录自启选择、运行时设置和有限崩溃恢复；没有发布独立 exe 或 GitHub Release。

主要参考 [yt-dlp 的发行文件和构建说明](https://github.com/yt-dlp/yt-dlp#release-files)：同一仓库和版本，分别产出平台适配的文件。自启接入参考 [Syncthing](https://docs.syncthing.net/users/autostart)：业务核心共享，接入各系统原生启动机制，不要求跨平台共用同一服务管理器。

下一阶段先添加 Windows/Linux CI，执行各自的离线用例及安装入口检查；再从同一 tag 用平台构建任务生成 Windows x64 / Linux x64 产物及校验和。优先打包目录而非单文件，便于随包带安装脚本、技能和数据文件；不复制用户登录、密钥或状态。具体构建工具尚未实现。

Python 工具可评估 PyInstaller，它可以带上 Python 与依赖，让使用者不用另装 Python，但[官方文档](https://pyinstaller.org/en/stable/operating-mode.html)要求在目标 OS 构建，不能把 Linux 本地通过等同于 Windows 包可用。现有依赖外部 Python 启动 supervisor/桥接的入口需先适配冻结程序，不能只改扩展名就发布。Codex CLI 的安装、登录、版本兼容，以及 curl 的供应仍需单独处理，不把它们打包后的可用性当成既成事实。
