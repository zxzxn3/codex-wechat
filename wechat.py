"""iLink message adapter; see docs/SOURCES.md for protocol provenance."""
import asyncio
import base64
import json
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from errors import WeChatAuthExpired
from diagnostics import log_event
from subprocess_io import communicate
from platform_support import curl_command, subprocess_options
import wechat_auth
from wechat_auth import identity

BASE_URL = "https://ilinkai.weixin.qq.com"
VERSION = "0.1.0"


@dataclass(frozen=True)
class LoginResult:
    account: dict
    previous: dict | None
    identity_changed: bool
    already_connected: bool = False


def trusted_url(value):
    url = urllib.parse.urlsplit(value)
    # Never send the bot credential to an arbitrary redirect host.
    if (url.scheme != "https" or url.username or url.password or url.port
            or not (url.hostname == "ilinkai.weixin.qq.com"
                    or (url.hostname or "").endswith(".ilinkai.weixin.qq.com"))
            or url.path not in ("", "/") or url.query or url.fragment):
        raise RuntimeError("微信返回了不受信任的 API 地址")
    return value.rstrip("/")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class WeChat:
    def __init__(self, account=None):
        self.account = account or {}
        self.base = trusted_url(self.account.get("base_url", BASE_URL))
        self.opener = urllib.request.build_opener(NoRedirect)

    async def request(self, endpoint, body=None, *, auth=True, timeout=40, request_id=None):
        # A thread blocked in urllib survives task cancellation and delays
        # asyncio.run shutdown. A curl child can be terminated immediately.
        headers = {"iLink-App-Id": "bot", "iLink-App-ClientVersion": "256"}
        if body is not None:
            headers.update({"Content-Type": "application/json",
                            "AuthorizationType": "ilink_bot_token",
                            "X-WECHAT-UIN": base64.b64encode(
                                str(secrets.randbits(32)).encode()).decode()})
        if auth:
            headers["Authorization"] = "Bearer " + self.account["token"]
        config = ["url = " + json.dumps(self.base + "/" + endpoint)]
        config.extend("header = " + json.dumps(key + ": " + value)
                      for key, value in headers.items())
        if body is not None:
            payload = dict(body)
            if auth:
                payload["base_info"] = {"channel_version": VERSION,
                                        "bot_agent": "CodexWechat/" + VERSION}
            config.append("data = " + json.dumps(json.dumps(payload)))
        started = time.perf_counter()
        proc = await asyncio.create_subprocess_exec(
            curl_command(), "--disable",
            "--silent", "--show-error", "--proto", "=https",
            "--connect-timeout", str(min(timeout, 10)), "--max-time", str(timeout),
            "--max-filesize", str(16 * 1024 * 1024),
            "--write-out", "\n%{http_code}", "--config", "-",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, **subprocess_options())
        output, _ = await communicate(proc, ("\n".join(config) + "\n").encode(), timeout=timeout + 2)
        elapsed = time.perf_counter() - started
        # Do not record request bodies, URLs, tokens or response content.
        if proc.returncode or (endpoint != "ilink/bot/getupdates" and elapsed >= 2):
            print(f"微信请求 {endpoint.split('?')[0]}: curl={proc.returncode} elapsed={elapsed:.2f}s",
                  file=sys.stderr, flush=True)
        if proc.returncode:
            log_event("wechat.request", level="warning", component="wechat",
                      endpoint=endpoint.split("?")[0], ok=False,
                      curl_code=proc.returncode, duration_ms=elapsed * 1000,
                      request_id=request_id)
            raise RuntimeError(f"微信网络请求失败（curl {proc.returncode}）")
        http_status = None
        try:
            raw, status = output.rsplit(b"\n", 1)
            status = int(status)
            http_status = status
            if not 200 <= status < 300:
                log_event("wechat.request", level="warning", component="wechat",
                          endpoint=endpoint.split("?")[0], ok=False,
                          http_status=status, duration_ms=elapsed * 1000,
                          request_id=request_id)
                raise RuntimeError(f"微信 HTTP {status}")
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError()
        except (ValueError, TypeError):
            log_event("wechat.request", level="warning", component="wechat",
                      endpoint=endpoint.split("?")[0], ok=False,
                      http_status=http_status, duration_ms=elapsed * 1000,
                      request_id=request_id)
            raise RuntimeError("微信响应无法解析") from None
        if result.get("ret", 0) != 0 or result.get("errcode", 0) != 0:
            code = result.get("errcode") or result.get("ret")
            log_event("wechat.request", level="warning", component="wechat",
                      endpoint=endpoint.split("?")[0], ok=False,
                      http_status=http_status, error_code=code,
                      duration_ms=elapsed * 1000, request_id=request_id)
            if auth and str(code) == "-14":
                raise WeChatAuthExpired()
            raise RuntimeError(f"微信 API 错误 {code}")
        # Long-poll requests are routine background traffic. Successful polls
        # stay at debug even though each one normally waits ~18 seconds.
        level = "debug" if endpoint.startswith("ilink/bot/getupdates") else "info"
        log_event("wechat.request", level=level, component="wechat",
                  endpoint=endpoint.split("?")[0], ok=True, http_status=http_status,
                  duration_ms=elapsed * 1000, request_id=request_id)
        return result

    async def poll(self, cursor):
        return await self.request("ilink/bot/getupdates", {"get_updates_buf": cursor})

    async def send(self, entry):
        return await self.request("ilink/bot/sendmessage", {"msg": {
            "from_user_id": "", "to_user_id": entry["user"],
            "client_id": entry["id"], "message_type": 2, "message_state": 2,
            "context_token": entry["context"],
            "item_list": [entry["item"]] if "item" in entry else
                         [{"type": 1, "text_item": {"text": entry["text"]}}]}},
            request_id=entry.get("requestId"))

    async def typing_ticket(self, user, context=None):
        body = {"ilink_user_id": user}
        if context:
            body["context_token"] = context
        result = await self.request("ilink/bot/getconfig", body, timeout=10)
        ticket = result.get("typing_ticket")
        return ticket if isinstance(ticket, str) else ""

    async def set_typing(self, user, ticket, status):
        await self.request("ilink/bot/sendtyping", {
            "ilink_user_id": user,
            "typing_ticket": ticket,
            "status": status,
        }, timeout=10)


def print_qr(url):
    from platform_ui import show_qr
    if not show_qr(url):
        print("未能绘制二维码，可使用二维码工具打开下面的链接。")


async def login(store, *, starting=False, rebind=False,
                before_identity_change=None, persist=True):
    """Acquire credentials; rebind defers local writes until its commit lock."""
    client = WeChat()
    previous = store.load("account")
    local_tokens = ([str(previous["token"])]
                    if previous and previous.get("token") else [])
    qr = await client.request("ilink/bot/get_bot_qrcode?bot_type=3",
                              {"local_token_list": local_tokens[-10:]}, auth=False)
    url = qr["qrcode_img_content"]
    print("用微信扫描二维码绑定账号并确认授权，二维码时效120s。\n"
          "已绑定Clawbot的微信会在微信端确认后被替换。\n"
          "同号再次扫码无效，使用ctrl+c取消等待。", flush=True)
    print_qr(url)
    print(url, flush=True)
    deadline = time.monotonic() + 150
    verify = None
    while time.monotonic() < deadline:
        query = {"qrcode": qr["qrcode"]}
        if verify:
            query["verify_code"] = verify
        try:
            data = await client.request("ilink/bot/get_qrcode_status?" +
                                        urllib.parse.urlencode(query), auth=False)
        except RuntimeError:
            await asyncio.sleep(2)
            continue
        status = data.get("status")
        if status == "confirmed":
            account = {"token": data["bot_token"], "user": data["ilink_user_id"],
                       "bot": data["ilink_bot_id"],
                       "base_url": trusted_url(data.get("baseurl", BASE_URL))}
            if not account["user"] or not account["token"]:
                raise RuntimeError("微信登录响应缺少身份或凭据")
            identity_changed = bool(previous and identity(previous) != identity(account))
            if identity_changed and not rebind:
                raise RuntimeError(
                    "已有微信绑定；请运行 codex-wechat rebind 重新绑定。")
            if persist and identity_changed and before_identity_change is not None:
                before_identity_change(previous, account)
            if persist:
                store.save("account", account)
                print("绑定成功。只接受此次扫码用户的私聊；" +
                      ("正在继续启动桥接。" if starting else "运行 start 启动桥接。"))
            return LoginResult(account, previous, identity_changed)
        if status == "scaned_but_redirect":
            host = data.get("redirect_host", "")
            client.base = trusted_url(host if host.startswith("https://") else "https://" + host)
        elif status == "need_verifycode":
            verify = await asyncio.to_thread(input, "请输入手机显示的配对码（仅本机）：")
        elif status == "binded_redirect":
            if not previous or not previous.get("token") or not previous.get("user"):
                raise RuntimeError("微信报告已绑定，但本机没有可用凭据；请检查状态目录")
            state = store.load("bridge", {})
            if (wechat_auth.state(state) == wechat_auth.AUTH_REQUIRED or state.get("bindingPending")):
                # QR status alone does not prove that a previously rejected token
                # works. getconfig authenticates without consuming the message cursor.
                await WeChat(previous).request("ilink/bot/getconfig",
                                               {"ilink_user_id": previous["user"]}, timeout=10)
            print("服务端确认此微信机器人已绑定；本地凭据保持不变。", flush=True)
            return LoginResult(previous, previous, False, already_connected=True)
        elif status in ("expired", "verify_code_blocked"):
            raise RuntimeError("二维码已过期或验证受限；请重新运行 login")
        await asyncio.sleep(1)
    raise RuntimeError("扫码等待超时，请重新运行 login")
