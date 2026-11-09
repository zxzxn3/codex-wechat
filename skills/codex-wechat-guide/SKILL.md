---
name: codex-wechat-guide
description: Explain how to use the codex-wechat WeChat bridge (微信桥/微信命令), including starting tasks, sessions, switching, models, approvals, attachments, stopping, and recovering from common input mistakes.
---

# codex-wechat 使用说明

当用户询问这个微信桥怎么用、某个命令做什么，或命令输入失败时使用本 skill。用用户的语言回答，给出可直接发送的完整命令；不要编造命令或参数。

## 开始使用

- 直接发普通微信消息即可开始任务，不需要先发命令。
- 默认会先回一行 `IN ...` 接收提示，处理过程中可能有进度或中间回复，最后是正式回答，可能再跟一行 `OUT ...` 结束提示。
- 完整命令列表让用户发送 `/help all`；设置说明发送 `/help settings`。

## 常用操作

| 目的 | 发送 | 说明 |
| --- | --- | --- |
| 查看状态 | `/status`（`/s`） | 后端、会话、模型、强度、目录、排队和用量 |
| 列出会话 | `/sessions` | 带编号的会话列表 |
| 搜索/翻页 | `/sessions 关键词` 或 `/sessions 2` | 编号只对应最近一次显示的列表 |
| 切换会话 | `/switch 1` | 先 `/sessions`；也可用 ID 前缀或搜索词 |
| 新建会话 | `/new` | 开始一个新会话 |
| 压缩上下文 | `/compact` | 等待“正在压缩”以及结束/中断提示 |
| 停止并清空排队 | `/stop` | |
| 排队到当前任务后 | `/queue 吃饭了吗` | 显式排一条稍后执行的消息 |
| 撤回最后一条排队 | `/unqueue` | |
| 清空未发送回复 | `/discard` | |
| 查看模型 | `/model` | 带编号的模型列表 |
| 切换模型 | `/model 1` | 编号对应最近一次 `/model` 列表 |
| 查看/设置强度 | `/effort` 或 `/effort high` | 使用列表里显示的档位 |
| 查看/设置目录 | `/cwd` 或 `/cwd ~/project` | 附件必须位于当前工作目录内 |
| 查看/切换后端 | `/backend` 或 `/backend codex` | 使用显示的后端名称 |
| 查看设置 | `/settings` | 例：`/settings autostart true` |
| 重启微信桥 | `/restart` | 需要服务所有者；有任务时先 `/stop` |

## 审批与提问

- 审批提示里会直接显示真实的 `/approve <审批码>` 和 `/deny <审批码>`，让用户照抄整行；审批码是短随机码，不是编号。
- 原生提问里会显示 `/answer <问题码> <选项或文字>`；可以让用户回复选项编号，也可以直接填文字。
- 不要让用户在审批码或问题码外面加花括号。

## 附件

- `/get ./file.png` 取回图片或文件；`/getfile ./file.zip` 强制按文件发送。
- 只能取回当前工作目录内的文件；需要时先让用户用 `/cwd` 切换目录。代理侧的附件格式由 `wechat-attachments` skill 说明。

## 输入错误

- 用法里的 `{}`、`[]` 是占位符，不是要输入的内容。例如发送 `/switch 1`，不要发送 `/switch {1}`。
- 回复里以 `例：` 开头的那一行就是可直接照抄的例子。
- 命令被拒绝时，告诉用户正确写法和下一步即可，不要解释内部 worker、writer、detach 或队列机制。

## 边界

- 没有桥接回复确认时，不要声称某个命令已经执行。
- 不要一次倾倒全部命令；用户要看完整列表时再让用户发送 `/help all`。
- 不要暴露内部状态机、队列实现或凭据；只说明用户看到的结果和下一步。
