# 配置、资源与安装边界

本文说明热配置、提示模板、资源清理和后台回落。配置项与命令入口分别见
[COMMANDS](COMMANDS.md) 和 [INSTALL](INSTALL.md)，运行期所有权见
[ARCHITECTURE](ARCHITECTURE.md)，交付状态见 [DELIVERY](DELIVERY.md)。

## 热配置与提示

当前可热修改的数值设置及其范围见
[命令与设置参考](REFERENCE.generated.md#数值设置)。

热配置从同一JSON按inode/mtime/size变化读取，整组校验后应用；无效文件保留旧值。
校验失败保留 stderr 提示，并写 config.reload_failed 安全事件（仅失败类别与异常类型）；
Bridge 将一次失败提示入发件箱。同一文件签名不重复报告，无微信上下文时暂存提示标记；
有效配置重新加载后清除该标记。失败提示使用最后生效的文案快照，不递归触发配置读取。
实际文件键变化才清对应微信模板覆盖。显示快照不成为原生设置的隐式写入来源。
运行期文案唯一目录为prompts.py的DEFAULT_PROMPTS，config.prompts仅存覆盖；不重载代码模块。
HotSettings在应用前统一验证键、文本类型、字节上限及简单占位符语法；删除键恢复默认。
Bridge.prompt按需重读配置，仅从现有状态快照与局部参数渲染一次；后端回调使用同一入口，
无入站消息也能读取新文案。纯展示函数可注入该入口，独立后端默认使用目录原文。
帮助描述也在提示目录维护，命令元数据仍只在command_specs声明；不把用户文案当成路由或状态。
展示标签与语义字段键分离，不改变审批token、原生内容或交付分组。
模板共享字段和局部参数统一使用英文snake_case，不保留旧中文字段映射或旧模板自动改写。
默认模板、共享快照、字段列表和渲染调用采用同一套名称，显示标签保持原文。
Token优先原生事件，缺失时只读当前Thread.path的有界尾部，按文件身份/时间/大小缓存；
该本地格式不是稳定协议，不扩大依赖。限额属于账户缓存，不依赖子进程存在。

## 取消与资源清理

取消必须先完成子进程退出、stdout/stderr回收与stdin关闭，再向调用者传播；第二次取消
也不遗弃清理任务。共享连接关闭仅结束自己的代理。原子JSON临时文件成功/失败都清理。
传输临时目录即时清理；已保存附件与用户原文件不按年龄自动删除。

## 共享后台回落

共享 daemon 启动失败时，本次运行按设计回落独立进程；原因保留在 `/status` 的
`shared_fallback` 字段和 `backend.shared_unavailable` 日志中，持久 `sharedDaemon`
设置不被改写。Linux DeepSeek 密钥环失败也走这一回落路径；若产品要求 fail-fast，应作为
独立设计变更，而不是假定错误已被静默吞掉。

## 安装与服务边界

安装器保持准备阶段独立、激活集中，不初始化账户、不默认开启自启、不重启业务、不提供回滚。
安装、更新和卸载的稳定入口、数据保留和 `current` 损坏处理见 [INSTALL](INSTALL.md)。
服务启动的桥接子进程由 systemd unit 或 Windows supervisor 设置
`CODEX_WECHAT_SERVICE_CHILD=1`，因此不会被用户前台 `start` 的互斥检查误伤。
`logs --service` 保持平台原生来源：Windows 为 `service.log`，Linux 为
`journalctl --user -u codex-wechat`。Windows 的 `service.log` 按 5 MiB 轮转，
保留份数与 `bridge.jsonl` 共用 `logs.keep`；Linux 控制台日志的保留由 journald 管理。
