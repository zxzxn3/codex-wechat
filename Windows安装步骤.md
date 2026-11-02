# Windows 安装步骤

本副本基于 df8d949，已临时移除 Codex CLI 安装检查，用于尝试直接使用 Codex App；后端发现与连接逻辑未改，能否直接连接尚待实测。

## 1. 准备

- 使用 Python 3.11+，可在 PowerShell 执行 `py -3 --version` 查看。
- 执行 `curl.exe --version`，运行微信通道需要 8.4+。
- 准备好已登录的 Codex；本次不要求先另外安装 CLI。
- 本地源码安装不需要 Git，也不需要 clone。

## 2. 安装

打开普通 PowerShell，不需要管理员权限：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
cd D:\codex-wechat-install-df8d949
py -3 installer.py
```

选择 `1. 安装 / 更新`，查看计划并确认。安装器会识别已有安装，保留配置和用户数据；安装时不扫码、不启动程序，新安装自启默认关闭。

只想先看流程，可运行 `py -3 installer.py --dry-run`。

## 3. 启动并扫码

```powershell
& "$env:LOCALAPPDATA\codex-wechat\app\codex-wechat.ps1" start
```

未绑定微信时，程序才会引导扫码；已有登录则直接使用。观察手机扫码界面是否允许选择原机器人，目前不保证两侧扫码会自动继承同一机器人身份。

先在微信发送 `/help`、`/status`，再发送普通消息，确认微信通道与模型连接分别能否使用。若模型启动失败，保留完整报错，不代表安装本身失败。

## 4. 需要时开启自启和后台运行

确认能正常交流后，在微信发送：

```text
/settings autostart on
```

需要立即转到后台运行时，先在 PowerShell 按 Ctrl+C 结束前台程序，再执行：

```powershell
& "$env:LOCALAPPDATA\codex-wechat\app\codex-wechat.ps1" service-start
```

遇到旧测试安装或计划任务归属冲突时，先保留报错，不要手动删除目录或任务。

## 本机后端修复（2026-11-02）

微信绑定与原聊天入口已在本机成功连接。仅移除安装器的 CLI 检查，不会让运行时自动发现 App 自带的 CLI。
本机配置已显式指向 Codex App 包内 app/resources/codex.exe，并设置后端代理 http://127.0.0.1:7897。
这是独立 app-server 后端，不表示已支持接管桌面 App 的活动会话；App 更新更换包路径后，需重新确认此配置。

源码已补上微信 HTTP 子进程取消后的管道回收，并让后端错误日志显示安全分类原因。
当前 Codex 执行环境无法读取现有安装的版本目录，未改目录权限；在普通 PowerShell 执行本机修复助手完成更新：

```powershell
& 'C:\Users\zxzxo\CodexProxy\Repair-Wechat.ps1'
& "$env:LOCALAPPDATA\codex-wechat\app\codex-wechat.ps1" start
```

不需要重新扫码。启动后先看 /s，再发送普通消息检查模型通道；Ctrl+C 时检查是否还有管道清理异常。
本轮已做修改文件的 Python 语法检查，未执行测试套件或真实模型请求。
