# 验证状态

更新：2026-11-10。离线结果、真实平台行为和外部协议证据分别记录；mock 不能替代 Windows、
微信、CDN、App 或模型验收。

## 当前证据

- Linux 检查入口：`scripts/check.sh` 包含 Ruff、Pyright、ShellCheck、分支覆盖单元测试和
  生成文档漂移检查。结果按日期与提交记录，不作为永久测试数基线。
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
- 2026-11-10 Linux 当前树新增了状态损坏隔离、不完整运行时布局拒绝、只读 status 诊断和
  Git `HEAD` release 边界。解析器资源上限与极深状态复制明确采用 fail-fast，不维护隔离
  承诺；原地换根参数和旧标记兼容已移除。Windows 嵌套重解析点清理只有静态回归，目标
  Windows 入口、junction 行为和真实 Scheduled Task 仍需实机复测。
- Windows 历史提交 `ab6ab61`：完整离线测试和 Ruff 通过；`057a96f` 也有较早的完整报告。
  这些结果不能证明当前树的 Windows 路径已复测。
- Windows 服务生命周期曾实机验证 PowerShell 5.1、Scheduled Task、Job Object 清理、单实例、
  通知和命令入口互斥；均缺少可核对到当前提交的完整证据。
- Windows App IPC 曾验证 writer 冲突、身份核对和无 App / App 已持有 / App 后启动状态；
  仍受 App 版本影响。
- Windows 安装、更新、重启、提权拒绝和卸载曾有实机报告，但部分提交号未记录；当前 HEAD
  仍需在目标机器复测。
- Linux 安装、入口、更新、配置保留和卸载有隔离环境与离线回归；自定义数据根和 systemd
  组合仍需真实用户管理器验收。
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
- Windows 命名管道存在性、App 更新后的 IPC 兼容、三端并发和 `service.log` 连续轮转仍需
  实机验证。
- 非 ASCII 用户目录下的 Windows `.cmd`、Linux GNOME 代理探测和 Windows 共享后台 WMI
  路径只有在目标环境实测后才能写成完整通过。
- 图片、引用、原生问题和审批在最新 bot 上没有逐项复测；旧 bot 和早期窗口的结论仍作为历史
  证据保留。
- 微信原生语音气泡受上游静默过滤限制，当前不是可交付能力。
- 附件回传依赖 `wechat-attachments` skill；未安装时模型可能产生裸路径文本。

事件时间线、统计和脱敏记录见
[2026-11-06 静默丢弃档案](incidents/2026-11-06-wechat-silent-drop.md)。
