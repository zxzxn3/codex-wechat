# 命令与设置参考

> 本文件由 `scripts/reference_docs.py` 从命令目录和设置边界生成。
> 修改实现后运行该脚本更新；`scripts/check.sh` 会用 `--check` 检查漂移。

## 微信命令短别名

| 短名 | 完整命令 |
| --- | --- |
| `/s` | `/status` |
| `/v` | `/version` |
| `/n` | `/new` |
| `/c` | `/compact` |
| `/q` | `/queue` |
| `/u` | `/unqueue` |
| `/d` | `/discard` |
| `/p` | `/ping` |
| `/b` | `/backend` |
| `/m` | `/model` |
| `/e` | `/effort` |
| `/a` | `/answer` |
| `/g` | `/get` |
| `/r` | `/restart` |
| `/` | `/continue` |
| `/h` | `/help` |

## 全部微信命令

| 命令 | 用途 |
| --- | --- |
| `/status` | 当前状态 |
| `/version` | 查看微信桥版本 |
| `/settings` | 查看设置 |
| `/sessions` | 列出、搜索或翻页会话 |
| `/new` | 新建会话 |
| `/compact` | 压缩当前会话上下文 |
| `/queue` | 排队到当前任务结束后执行 |
| `/unqueue` | 撤回最后一条尚未开始的排队消息 |
| `/stop` | 停止并清空排队消息 |
| `/discard` | 清空待发送消息 |
| `/ping` | 微信与供应商网络延迟 |
| `/switch` | 按编号、ID前缀或搜索词切换会话 |
| `/backend` | 查看或切换后端 |
| `/model` | 查看或切换模型 |
| `/effort` | 查看或切换强度 |
| `/cwd` | 查看或切换目录 |
| `/approve` | 同意审批 |
| `/deny` | 拒绝审批 |
| `/answer` | 回答原生提问 |
| `/get` | 取回当前工作目录内的文件、图片或视频 |
| `/getfile` | 作为文件回传 |
| `/restart` | 重启微信桥 |
| `/continue` | 继续发送因额度暂停的回复 |
| `/help` | 查看常用命令；`/help all` 查看全部；`/help settings` 查看设置说明 |

## 数值设置

| 设置 | 默认值 | 范围 |
| --- | ---: | --- |
| `connectionIdleSeconds` | 60 | 0–3600 |
| `media.maxMiB` | 20 | 1–100 |
| `slicing.targetChars` | 600 | 80–20000 |
| `logs.keep` | 3 | 1–100 |
| `sending.replyBudget` | 9 | 1–100 |

## 出站保护

| 保护 | 当前值 |
| --- | ---: |
| 连续出站回复间隔 | 2.5s |
| `delivery=unconfirmed` 的连续失败阈值 | 3 |

> 以上数值由 `codex_wechat.configuration`、
> `codex_wechat.output_delivery` 和 `codex_wechat.commands` 提供，
> 不在手写文档中维护副本。
