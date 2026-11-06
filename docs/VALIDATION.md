# 验证状态

更新：2026-11-06。文档、源码核对和 Linux mock 不能替代真实 Windows、微信、CDN 或模型验收。
Windows 行只有明确记录可核对的提交号时才作为当前 HEAD 证据；否则只算历史/Windows 侧报告。
本页只记录当前状态和缺口；详细历史日志见 git 提交历史。

| 范围 | 当前证据 | 状态 |
|---|---|---|
| 离线单测 | `python -m unittest discover -s tests`：Linux 910 项、890 通过、20 跳过、0 失败（2026-11-06 当前树）；Windows 侧曾有 894 项、875 通过、19 跳过、0 失败的报告，但未记录可核对的提交号，不能作为当前 HEAD 的独立证据；新增安装后 stable payload/import 烟测、数据根冲突、平台用户目录回退和服务状态矩阵；套件把诊断写入临时 sink，不污染数据根下的 `logs/bridge.jsonl` | 已验证 |
| 静态检查 | ruff、shellcheck、`py_compile`、pyright（0 errors、5 个环境导入 warning） | 已验证 |
| Windows 服务基础生命周期 | PowerShell 5.1、Scheduled Task、Job Object 清理、单实例、通知 | 历史证据，无当前提交号；不作为当前 HEAD 证据 |
| Windows App IPC | writer 冲突连接、身份核对、无 App / App 已持有 / App 后启动三种命名管道状态 | 历史证据，无当前提交号；仍受 App 版本影响 |
| Windows 真实微信 | 文本、图片、文件、语音、引用、原生提问、审批 | 历史证据，无当前提交号；不作为当前 HEAD 证据 |
| Windows 新命令入口 | 2026-11-06 在历史 Windows 构建上实机复测（当前 Linux 仓库不能复核该构建的提交号）：`.cmd` 短入口在 cmd.exe 与 PowerShell 均可用；无参数默认 `status` 且输出一份 JSON（`service` 段含 `state`/`last_result`）；`service-stop`/`service-status`/`desktop`/`autostart on`/`status -Json` 全部被拒；`start` 在服务运行时 exit 78，`service-start` 在前台持锁时 exit 78；`logs` / `logs -Service` 分别读 `bridge.jsonl` / `service.log`；版本指针拒绝大写 ID、`..`、短 ID、17 位 | 已实机验证（历史构建） |
| Windows 安装/更新（数据根标记） | Windows 侧报告过一次实机验证：`installer.py update --source . --dir %LOCALAPPDATA%\codex-wechat\app` 写出 `.data-root`（内容为平台用户目录）、激活新版本并重配计划任务；同一源码再跑一次走“已经安装，没有修改”分支；`service-restart` 后 `status` 为 `bound: true`、`auth: valid`、`delivery: ok`、`unsent: 0`、`state: Running`。顺带修掉一个只在中文 Windows 出现的缺陷：服务子进程带 `PYTHONUTF8=1`，`icacls` 的本地化输出按 UTF-8 解码会在读取线程里抛 `UnicodeDecodeError`，每次启动都往 `service.log` 写一段 traceback（不影响 ACL 生效），现已显式指定编码并用 `errors="replace"`；具体提交号未记录，当前 HEAD 仍需 Windows 复测 | 历史证据，不作为当前 HEAD 证据 |
| Windows 卸载/重启 | Windows 侧报告：`service-restart` 跨"正在停止"状态 18.6 s 后 `backend.connected`；配置无效时 32.8 s 后 exit 78 且 `last_result=78`（不假成功）；`current` 损坏时 `uninstall` 仍 exit 0；`uninstall` 保留 7418 个数据文件；`uninstall -PurgeData` 删除已知数据项且重建后需重新扫码；通过短入口卸载 exit 0、stderr 无输出、入口在数秒后消失 | 历史证据，无提交号；当前 HEAD 待复测 |
| Windows 提权拒绝 | 提权令牌下 `status`、`logs` 放行（只读例外），`autostart`、`service-start`、`service-restart`、`update` 均 exit 78 并提示「请以普通用户身份运行，不要使用 root/sudo 或管理员终端。」。`service-start` 由操作者在管理员终端实际撞到 | 历史证据，无提交号；当前 HEAD 待复测 |
| App / CLI / 微信三端并发 | 2026-11-06 实机：App 侧发起后 `backend.writer_kept status=followed` 成功跟随 App 会话，App 回复经 `output.enqueued`（body + completion）进入发件箱，即"跟随 + 转发"跑通；但这几条出站被服务端静默丢弃（见下一行）；微信↔CLI 双向不在本次范围——该能力此前已在 `sharedDaemon: true` 的窗口里验证过（微信→CLI 与 CLI→微信均通，见会话记录），Windows 默认关闭是设计选择（面向偏好 App 的用户，且避免影响 App）；此后改动很多，重新启用需复测 | 历史证据，无提交号；部分覆盖，当前 HEAD 待复测 |
| 出站投递确认 | 2026-11-06 实机正面撞上：bot 文本出站被服务端静默丢弃，`sendmessage` 一律返回 HTTP 200 + `{"ret":0}` 且无消息 id，同期 `sendtyping`、`getconfig` 全程正常，重启服务无效，手工直连 API 也全部未送达。原实现只看 HTTP 状态，`output.sent` 照记，回复离开发件箱而用户看不到任何东西。历史修复先改为"无消息 id 即未投递"，并实机验证了当时的 3 次重试策略：`send_ack no_id` → `output.send_failed error_type=WeChatNotAcknowledged` ×3 → `output.failed`。2026-11-06 后续再收紧为：首次 no-id 即隔离为 `delivery_unknown`，持久暂停出站，不发失败通知、不自动重试，重启也不重放；下一条入站只恢复新回复，旧积压移入 `staleOutbox` 不发送。只有传输错误按 1s/3s 重试。该最新策略目前只有离线单测，尚未在真实微信上复测。**19:27Z 用另一个微信（小号）`rebind` 后立即恢复**：19:27:41Z 起连续 7 条全部 `acked` → `output.sent`，`unsent` 归零；附件同一判据也成立：19:35Z 与 19:37Z 两条 `role=asset` 均为 `acked` → `output.sent` | 历史重试行为与换号恢复已实机验证；首次即隔离的新策略与 stale 恢复闸门待实机；封禁按"账号 + 时间"生效，不是永久标记 |
| CDN 上传重试幂等 | 只有客户端超时会在同一 `filekey` 上重试；RST 与服务端非 200 都会换 key 重传。固定 `filekey` + 固定 AES 密钥连传两次得到两个不同引用，且 bot 侧回读恒 400，故幂等未证实 | 已实测，结论为"未证实" |
| WMI 凭据路径 | 替身进程和直接 helper 已验证；本机计划任务的 Job 允许 breakaway，`daemon start` 直连成功，真实 daemon 走 WMI 的路径不可触发 | 部分验证，条件不可复现 |

## 已知缺口

- Windows 命名管道存在性、App 版本更新后的 IPC 兼容和三端并发需要在目标机器复测；
- Windows `service.log` 的 5 MiB 连续轮转、`logs.keep` 热改后的轮转链修剪，以及
  `logs -Service` 跟随多份轮转文件目前只有 Linux 离线单测覆盖；需在 Windows 实机验证。
- Linux systemd 自定义数据根现在通过安装根 `.data-root` 解析，服务不再保存第二份路径；
  仍需在真实 systemd 用户管理器下验证 service-start 和 marker 更新后的服务启动。
- 出站静默丢弃在本机已发生两次，序列是：更早的开发期用**小号**时被封 → 换**大号**继续开发 →
  2026-11-06 18:53Z 大号被封 → 19:27Z 换回**小号**，此时小号已自愈可用；
  2026-11-06 11:49 CST 大号也已解封，`/s` 等消息可正常收发。可见这不是永久的账号标记：
  同一账号隔一段时间会恢复，社区报告的恢复时长从 2 小时 50 分到约 4 天不等（见
  [SOURCES](SOURCES.md) 的"微信下行投递"一节）。同号 `rebind` 对恢复无效（服务端回
  `binded_redirect`，登录流程复用旧 token、不发新 bot id，已实测）；
  官方与社区的结论都指向"下行可能被静默过滤且契约无法区分"，因此不要用"强制重登/重装/干净
  rebind"去救；报告里有效的手段只有换绑定账号或等待，并且**重试与密集扫码本身可能加重限制**；
  待验证：三端并发里微信↔CLI 双向（能力已验证过，但需要先在 `sharedDaemon: true` 下复测；
  Windows 默认关是设计选择）；
  2026-11-06 之后新增的能力目前**只有单测覆盖**，尚未真机复测：每条入站约 10 条的回复额度
  （`sending.replyBudget` 默认 9、`/` 续传提示；额度按**服务端应答次数**计，故障期的真实表现
  未实测）、`logs.keep` 热改、`backend.version` 版本记录、
  新增的短指令（`/n` `/c` `/u` `/b` `/r` `/d` `/g`）、以及发送与请求日志的降噪改动；
  新 bot（`bd29824c4f7d`）上已复测文本、文件、语音；图片、引用、原生提问、审批未逐项复测，
  2026-11-06 决定暂不测（模型配额原因），旧 bot 的历史窗口结论仍然有效；
  2026-11-06 11:49–12:01 CST 的再次静默失败时间线、统计和脱敏事件见
  [事件档案](incidents/2026-11-06-wechat-silent-drop.md)；
  另需注意：附件回传依赖 `installer.py components --component wechat-attachments` 装上的 skill，
  没装时模型只能猜格式（本机实测猜成裸路径 `WECHAT_MEDIA: C:\...` 而被当普通文本）；
  自动回传只接受当前工作目录或桥私有 media 目录内的非隐藏文件，目录外的文件必须用 `/get 路径`；
- CDN 上传 POST 在没有服务端幂等证据前，不能把自动重试写成已验收行为；上传永不就地重试，只在下载上重试；
- WMI 真实 daemon、计划任务上下文和带凭据系统代理三者同时成立的条件尚未完整复现；
- 共享后台回落路径的提示和日志有离线覆盖，但真实失败条件可能需要破坏 Codex 安装才能稳定制造。

提交或发布前，至少更新本表的“当前证据”列；不要把一次成功样本扩大成所有平台组合通过。
