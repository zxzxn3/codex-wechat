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
| `/new` | 新建 |
| `/compact` | 压缩当前会话上下文 |
| `/queue` | 排队到当前任务结束后执行 |
| `/unqueue` | 撤回最后一条尚未开始的排队消息 |
| `/stop` | 停止并清空待办 |
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

| 设置 | 范围 |
| --- | --- |
| `connectionIdleSeconds` | 0–3600 |
| `media.maxMiB` | 1–100 |
| `slicing.targetChars` | 80–20000 |
| `logs.keep` | 1–100 |
