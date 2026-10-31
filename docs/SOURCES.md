# 文档依据

查阅日期：2026-10-31。先查文档后实现，未用试错推定协议。

- [OpenAI Codex App Server](https://learn.chatgpt.com/docs/app-server)：stdio JSON-RPC、initialize/initialized、thread/start、thread/resume、turn/start、turn/interrupt、item/completed、turn/completed 与审批请求。当前在线文档未按本机 CLI 0.166.0 固定；本次已生成并查阅本机该版本的协议 JSON Schema，核对 thread/list、thread/read、model/list、thread/resume、turn/start 的参数与响应。这是接口文档核对，不是运行测试。模型及权限参数默认不覆盖。
- [腾讯微信插件 README](https://github.com/Tencent/openclaw-weixin/blob/main/README.zh_CN.md)：扫码登录及长轮询接收机制。
- [腾讯微信后端协议](https://github.com/Tencent/openclaw-weixin/blob/main/docs/protocol_zh_CN.md)：二维码 bot_type=3、请求头、getupdates、sendmessage、context_token、消息类型、-14 错误。
- [腾讯 api.ts](https://github.com/Tencent/openclaw-weixin/blob/main/src/api/api.ts)、[types.ts](https://github.com/Tencent/openclaw-weixin/blob/main/src/api/types.ts)、[login-qr.ts](https://github.com/Tencent/openclaw-weixin/blob/main/src/auth/login-qr.ts)：与协议文档对照阅读。查阅时 package.json 的版本为 2.4.10。没有直接导入 OpenClaw 插件，也没有声称插件的服务端接口构成稳定、完整契约。

本桥接自行声明 AgentWechat/0.1.0，不冒充腾讯插件版本。独立客户端的接入资格及微信客户端适用条件尚需扫码联调确认；若服务端拒绝该客户端，必须报告差异，不改成伪装官方客户端来绕过。

API 地址重定向仅允许 HTTPS 的 ilinkai.weixin.qq.com 及其子域；若真实返回其他微信域名，应先核对官方代码和域名用途再调整。禁用 HTTP 自动跳转，避免 token 跟随到任意地址。

新增会话管理依据：thread/list 明确指定 cli/vscode/appServer 来源，排除子代理；model/list 提供支持的 reasoningEffort；thread/start 和 thread/resume 响应返回实际 model、reasoningEffort、cwd。thread/resume 通过 config.model_reasoning_effort 应用强度覆盖，turn/start 显式传递选中会话的 model/effort/cwd。恢复旧会话时不再强制覆盖为桥接初始 cwd。

服务安装依据：本机 `~/.local/share/ime-cursor/{README.md,install.sh,ime-cursor.service}`，systemd 262 的 systemd.unit/systemd.service 手册及 [systemd.special](https://www.freedesktop.org/software/systemd/man/latest/systemd.special.html) 的 graphical-session.target。使用用户服务随图形登录启停，`Restart=on-failure`，不启用 linger。

DeepSeek 接入依据：本机 `codex-deepseek/README.md` 与 launcher.py 的 app-server 转发逻辑。启动器自行设置独立 CODEX_HOME 并从桌面密钥环检查密钥；桥接仅按命令启动，不读取密钥。

微信重启指令依据：[systemctl 262](https://www.freedesktop.org/software/systemd/man/latest/systemctl.html) 的 show/MainPID 与 --no-block：后者仅验证并排入作业，不等待作业完成。使用异步 restart 避免服务等待自身退出。仅做静态检查，未执行真实重启。

媒体实现依据：腾讯协议的 getUploadUrl/CDN 媒体流程，以及 src/cdn/upload.ts、src/cdn/cdn-url.ts、src/messaging/send.ts。入站兼容原始 16 字节 key 的 base64 和十六进制字符串的 base64，图片优先 image_item.aeskey；出站按上游把十六进制 key 字符串作 base64。CDN 上传 POST 密文，读取 x-encrypted-param，再单独发送媒体消息。

加解密依据 cryptography 50.x 官方 [Cipher/AES128/ECB](https://cryptography.io/en/50.0.0/hazmat/primitives/symmetric-encryption/) 及 [PKCS7](https://cryptography.io/en/50.0.0/hazmat/primitives/padding/)，本机包版本 50.0.2；附件输入依据已查阅的 Codex App Server 官方文档及本机 TurnStartParams schema 的 localImage/path。静态检查不代表 CDN、模型图片输入或附件收发已联调通过。

CDN 连接诊断：现场观察到网关到 CDN 的 IPv6 TCP 连接处于 SYN-SENT，DNS 返回多个 IPv6 地址排在 IPv4 前。Python 3.14 socket.create_connection 顺序尝试解析地址，原 60 秒超时可在多个地址上累计。修复改用本机 curl 8.23 的默认 Happy Eyeballs；[curl 手册](https://curl.se/docs/manpage.html) 核对 --connect-timeout、--max-time、--config、--max-filesize（8.4+ 可限制传输中大小）。未用真实附件做操作测试。

默认追加消息依据：已查阅的 OpenAI App Server 文档 Steer an active turn 段落和本机 TurnSteerParams schema。turn/steer 必须携带 expectedTurnId，接受与 turn/start 相同的 UserInput 数组，不允许模型/目录等 turn-level overrides。当前轮次已结束时拒绝追加；不把超时当成确定未投递。仅静态检查，未对运行中的会话做 steering 操作测试。
