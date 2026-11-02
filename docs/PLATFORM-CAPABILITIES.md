# 平台 UI 能力

更新：2026-11-02。`platform_ui.py` 只做二维码与桌面通知的原生适配；微信
登录、认证状态、服务状态和消息仍由共享层负责。

## 二维码

`show_qr(url) -> bool` 同步执行：

1. 优先调用 PATH 中的 `qrencode`，参数固定为
   `-t ANSIUTF8 -o -`，URL 只从 stdin 传入。
2. 缺失或执行失败时，尝试已安装的 Python `qrcode` 包输出 ASCII 二维码。
3. 两者都不可用时返回 `False`；调用者必须继续打印或保留原 URL。

Windows 没有可假定的系统自带二维码编码命令或公开 API，因此不会伪造
“系统二维码”。可选 Python 回退由运行时导入，`platform_ui.py` 不在模块
加载时依赖它。`qrcode` 声明在独立的 `requirements-qr.txt`；安装器仅在找不到
原生 `qrencode` 时为该版本的环境安装回退，必需依赖 `requirements.txt` 不含它。

依据：[qrencode](https://fukuchi.org/works/qrencode/)、
[qrcode](https://pypi.org/project/qrcode/)。

## 桌面通知

`await notify(title, body) -> bool` 是尽力而为的通知：

- Linux：调用 PATH 中的 `notify-send`，标题和正文通过 argv 传递，并在标题
  前使用 `--` 分隔选项。超时 3 秒。
- Windows：调用系统 Windows PowerShell 5.1 的原生
  `System.Windows.Forms.NotifyIcon.ShowBalloonTip`。标题和正文先编码为
  UTF-8/base64 放入进程环境，PowerShell 脚本通过 `-EncodedCommand` 传入，
  不把用户文本拼进命令行；超时 7 秒。
- 找不到工具、非零退出、超时或执行异常时返回 `False`，不写控制台来冒充
  已弹通知。任务取消会终止并回收子进程，然后传播取消。

Windows 使用任务栏气泡通知，不声称是 WinRT toast。微软文档明确：未打包
桌面应用若要从代码触发 toast，必须先在开始菜单/所有程序注册带
AppUserModelID 的快捷方式；当前安装器没有该注册，不能仅凭
`ToastNotificationManager` 存在就宣称 toast 可用。若以后由安装器注册稳定
AUMID，可再把 Windows 适配替换为 toast，无需改调用接口。

依据：[NotifyIcon.ShowBalloonTip](https://learn.microsoft.com/en-us/dotnet/api/system.windows.forms.notifyicon.showballoontip?view=windowsdesktop-9.0)、
[桌面 toast 的 AUMID 要求](https://learn.microsoft.com/en-us/windows/win32/shell/enable-desktop-toast-with-appusermodelid)、
[PowerShell 5.1 参数](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_powershell_exe?view=powershell-5.1)。

## 集成与验证边界

- `wechat.print_qr()` 调用 `platform_ui.show_qr()`；绘制失败时保留可点击的
  原始 URL。`service_adapter.notify()` 在持久化认证状态之后调用
  `platform_ui.notify()`，通知失败不改变待重绑状态，也不阻塞主流程。
- 本机验收只覆盖 mock 子进程、可选 Python 回退和 Linux 真实 `qrencode`
  存在性；没有弹出真实桌面通知。
- Windows 的 PowerShell、NotifyIcon 与实际通知可见性均未实机验证；Focus
  Assist、通知开关、会话隔离和无桌面环境仍可能导致返回失败或不可见。
