# 开发与分发

`codex-wechat` 现在是独立 Git 仓库，默认位于 `~/.local/share/codex-wechat`；不要再把它当作
cfg 工作树的一部分。对外更新时，在独立仓库中提交并推送，核对发布内容不包含私有数据；
cfg 只保存本机其它配置，不保存本项目源码。

当前远端为 `https://github.com/zxzxn3/codex-wechat.git`，发布阶段保持私有。内测入口使用
GitHub Contents API 和 fine-grained PAT 从同一仓库取得 bootstrap，不需要额外公开引导库；
Windows 开发从同一远端取得源码，切换环境前先同步，避免两份源码分叉。

内测 PAT 只授予该仓库 `Contents: Read-only`。bootstrap 使用临时 Authorization header
clone 到临时目录，再调用同一个本地安装器；token 不进入 argv、remote URL、更新来源、
持久配置或日志，临时 git config 在 clone 完成后立即删除。PAT 撤销后新安装与更新立即失效。

平台网络入口调用同一个安装器；依赖声明集中在 `installer.py` 和 requirements 文件。
原生二维码可用时不安装 Python 回退；附件 skill 由组件入口选择安装。

安装器不初始化账户、不默认开启自启、不启动或重启桥接；更新保留配置和已有自启状态。
新旧版本按各自清单验证，不用新排除规则重新定义旧版身份。下载、构建和原子写临时文件会清理；
仍可能被进程使用的旧版本和已保存的附件不作为临时垃圾自动删除。

产物不包含用户账户、状态、代理、工作目录、日志或 `.venv`。安装、更新和卸载的用户流程见
[INSTALL](INSTALL.md)。
