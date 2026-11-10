# 验证状态

更新：2026-11-11。离线结果、真实平台行为和外部协议证据分别记录；mock 不能替代 Windows、
微信、CDN、App 或模型验收。

## 当前证据

- Linux 检查入口：`scripts/check.sh` 包含 Ruff、Pyright、ShellCheck、分支覆盖单元测试和
  生成文档漂移检查。结果按日期与提交记录，不作为永久测试数基线。
- 2026-11-10 Linux 代码快照 `3905a08`：专用测试会话从隔离副本执行完整
  `./scripts/check.sh`，1035 项中 1014 通过、21 跳过、0 失败；Ruff、ShellCheck 和生成
  文档检查通过，Pyright 0 errors / 5 个平台依赖 warning，分支覆盖率 78%。
- 2026-11-10 Linux `141afe5`：`./scripts/check.sh` 执行 1049 项，1028 通过、21 跳过、
  0 失败/错误；Ruff、ShellCheck 和生成文档检查通过，Pyright 0 errors / 5 个可选依赖
  warning，分支覆盖率 79%。真实 Windows、systemd、微信和 CDN 不在这次结果范围内。
- 2026-11-10 Linux 代码快照 `5fc7782`：专用测试会话从隔离副本执行完整
  `./scripts/check.sh`，1020 项中 999 通过、21 跳过、0 失败；Ruff、ShellCheck 和生成文档
  检查通过，Pyright 0 errors / 5 个平台依赖 warning，分支覆盖率 78%。该快照覆盖
  SelectionLease、SendOutcome、turn/interaction lease 和 writer identity 收口。
- 2026-11-10 Linux 代码快照 `b60313a` / `399c9a6`：完整 `./scripts/check.sh` 仍为
  1020 项中 999 通过、21 跳过、0 失败，覆盖率 78%。前者验证 turn/interaction 内部模块
  （`backend_interactions.py` 93%、`backend_turns.py` 92%）；后者验证声明式 typed settings
  及现有别名、提示和读回语义，定向 41/41 通过。Ruff、Pyright、ShellCheck 和生成文档检查
  均通过。
- 2026-11-10 Linux 代码快照 `3905a08` 包含状态损坏隔离、不完整运行时布局拒绝、只读 status 诊断和
  Git `HEAD` release 边界。解析器资源上限与极深状态复制明确采用 fail-fast，不维护隔离
  承诺；原地换根参数和旧标记兼容已移除。Windows 嵌套重解析点清理只有静态回归，目标
  Windows 入口、junction 行为和真实 Scheduled Task 仍需实机复测。
- 2026-11-11 Windows 实机 `8c7741a`：`python -m unittest discover -s tests`
  1035 项、1008 通过、27 跳过、0 失败，ruff 干净。之前在
  Windows 上有 20 项失败，全部是测试假设 POSIX：夹具装作 Linux 根目录而代码按
  主机平台校验，现由 `install_platform()` 显式声明。同日匿名访问公开
  一行入口：raw 地址 200、脚本可执行；无终端时拒绝继续而不是半装。
- 2026-11-11 Windows 实机 `273cb32`、release `4a6696e1f83dc08a`：完整离线测试
  1038 项中 1011 通过、27 跳过、0 失败，ruff 干净。匿名公开一行安装成功；服务
  `status` 返回 bound、auth valid、delivery ok、state ok，`unsent` 为 0。真实微信 `/v`
  显示 release ID 和 Codex 版本；日志构建标识已切换为 release ID。Codex 0.166.0 上
  `thread/settings/update` 的 `{model, effort}` 与 `{cwd}` 均实测生效。
- Windows 历史提交 `ab6ab61`：完整离线测试和 Ruff 通过；`057a96f` 也有较早的完整报告。
  这些历史结果用于追溯，当前证据以上一节为准。
- Windows 服务生命周期曾实机验证 PowerShell 5.1、Scheduled Task、Job Object 清理、单实例
  和命令入口互斥；通知仍按尽力而为能力处理，不写成已验证。以上历史证据均缺少可核对到当前
  提交的完整生命周期记录。
- Windows App IPC 曾验证 writer 冲突、身份核对和无 App / App 已持有 / App 后启动状态；
  仍受 App 版本影响。
- Windows 安装、启动、提权拒绝和核心服务路径已有当前实机证据；更新后的服务生命周期和
  purge 卸载仍待当前布局复测。
- 2026-11-10 Linux `v1.0`（`9ddaa16`）：本机构建 release `5f82a9be2b43942c`，完成默认
  路径布局的安装更新、systemd restart 和 status 检查。该证据不代表自定义 XDG/数据根组合，
  也不代表匿名公开一行安装已验证。
- Linux 安装、入口、更新、配置保留和卸载有隔离环境与离线回归。默认路径下的
  systemd 组合已由上一项实机验证；自定义 XDG/数据根组合仍需真实用户管理器验收。
- 2026-11-10 实机卸载复现了 systemd 强停与卸载等待同为 20 秒的竞态；修复后，停机宽限和
  桥关闭总截止时间有离线回归，真实卸载仍需在目标机复测。
- 2026-11-10 审查确认 `--purge-data` 可能因 XDG 状态根与安装根重叠而误删程序，也确认
  shell 自定义 XDG 不会自动进入 systemd user manager。两项均有隔离回归；真实服务启动仍需
  在自定义 XDG 环境复测。
- 2026-11-10 卸载器改为按 systemd 实际 `FragmentPath` 移除 unit，不再用当前 shell 的
  `XDG_CONFIG_HOME` 猜测注册位置；自定义 XDG 的实机卸载仍需复测。
- App / CLI / 微信三端并发曾在 `sharedDaemon: true` 下实现双向收发；Windows 默认关闭共享
  模式，恢复启用后需要复测。

## 微信下行投递

2026-11-06 实机确认：bot 文本出站被服务端静默丢弃期间，`sendtyping` 和 `getconfig` 正常，
`sendmessage` 返回 HTTP 200 与 `ret=0`，但没有消息 ID；重启和手工直连均未恢复。换绑另一个
微信后立即收到连续文本和附件，说明限制按账号和时间生效，不是永久账号标记。

当前 no-ID 隔离、运行时暂停、积压保留和 `/discard` 策略有离线测试；真实微信上的这套新
处置仍需复测。社区也报告约 10 条主动回复后出现 `ret=-2`，但官方没有公开固定额度。

出站原生语音气泡已验证失败：MP3、AMR-NB 和 SILK 均上传成功并取得消息 ID，但接收端没有
文件或语音气泡。稳定分支因此把音频作为普通文件发送，实验代码保留在
`archive/voice-bubble-probe`。

## 部分验证与缺口

- CDN 上传 POST 的幂等性未证实；上传不在原位置盲目重试，下载可以重复。
- Windows 命名管道存在性和空闲发现已有历史本机实测；App 更新后的 IPC 兼容、多 home、
  三端并发和 `service.log` 连续轮转仍需实机验证。
- Windows 当前实机未逐项覆盖安装器交互菜单、运行中 `/switch` `/new` detach、审批和原生
  问题、App IPC 跟随、图片/文件/语音/引用真实收发以及卸载。`bootstrap.ps1` 在父进程捕获
  stdout/stderr 时的 native stderr 行为也仍需确认。
- 非 ASCII 用户目录下的 Windows `.cmd`、Linux GNOME 代理探测和 Windows 共享后台 WMI
  路径只有在目标环境实测后才能写成完整通过。
- 图片、引用、原生问题和审批在最新 bot 上没有逐项复测；旧 bot 和早期窗口的结论仍作为历史
  证据保留。
- 微信原生语音气泡受上游静默过滤限制，当前不是可交付能力。
- 附件回传依赖 `wechat-attachments` skill；未安装时模型可能产生裸路径文本。

事件时间线、统计和脱敏记录见
[2026-11-06 静默丢弃档案](incidents/2026-11-06-wechat-silent-drop.md)。
