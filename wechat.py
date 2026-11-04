"""iLink message adapter; see docs/SOURCES.md for protocol provenance."""
import asyncio
import base64
import json
import secrets
import sys
import time
import urllib.parse
from dataclasses import dataclass

from errors import WeChatAuthExpired, WeChatPollTimeout
from diagnostics import error_fields, log_event
from latency import timeout_stage
from wechat_http import PooledHTTP
from configuration import DEFAULT_CONNECTION_IDLE_SECONDS
import wechat_auth
from wechat_auth import identity

BASE_URL = "https://ilinkai.weixin.qq.com"
# This is the WeChat channel protocol identity, independent of the product version.
CHANNEL_VERSION = "0.1.0"
POLL_TIMEOUT = 35
POLL_TIMEOUT_GRACE = 5
MAX_POLL_TIMEOUT = 120
SEND_TIMEOUT = 5
TYPING_TIMEOUT = 2


def api_error_kind(code, message):
    """Fixed diagnostic labels only; message hints are not verified root causes."""
    if str(code) == "-14":
        return "auth_expired"
    hint = message.strip().casefold() if isinstance(message, str) else ""
    if hint in ("prepare failed", "unknown error"):
        return hint.replace(" ", "_")
    if hint in ("frequency limit", "frequency limited", "rate limit exceeded", "too many requests"):
        return "rate_limit_hint"
    return "other"


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


class WeChat:
    def __init__(self, account=None, *, idle_seconds=DEFAULT_CONNECTION_IDLE_SECONDS):
        self.account = account or {}
        self.base = trusted_url(self.account.get("base_url", BASE_URL))
        self.http = PooledHTTP(idle_seconds)
        self.poll_timeout = POLL_TIMEOUT

    def set_idle_seconds(self, seconds):
        self.http.set_idle_seconds(seconds)

    async def close(self):
        await self.http.close()

    async def request(self, endpoint, body=None, *, auth=True, timeout=40, request_id=None):
        headers = {"iLink-App-Id": "bot", "iLink-App-ClientVersion": "256"}
        if body is not None:
            headers.update({"Content-Type": "application/json",
                            "AuthorizationType": "ilink_bot_token",
                            "X-WECHAT-UIN": base64.b64encode(
                                str(secrets.randbits(32)).encode()).decode()})
        if auth:
            headers["Authorization"] = "Bearer " + self.account["token"]
        payload = None
        if body is not None:
            payload = dict(body)
            if auth:
                payload["base_info"] = {"channel_version": CHANNEL_VERSION,
                                        "bot_agent": "CodexWechat/" + CHANNEL_VERSION}
            payload = json.dumps(payload).encode()
        started = time.perf_counter()
        route = endpoint.partition("?")[0]
        metadata = {"endpoint": route, "request_id": request_id}
        ok, level = False, "warning"
        try:
            output, timings = await self.http.request(
                self.base + "/" + endpoint, headers, payload,
                timeout=timeout, polling=route == "ilink/bot/getupdates")
            metadata.update(timings)
            if metadata.get("curl_code"):
                if (route == "ilink/bot/getupdates" and metadata["curl_code"] == 28
                        and timeout_stage(metadata) == "request_or_response"
                        and metadata.get("pretransfer_ms", 0) > 0
                        and metadata.get("first_byte_ms") == 0
                        and output in (b"\n0", b"\n000")):
                    raise WeChatPollTimeout("微信长轮询等待到期")
                raise RuntimeError(f"微信网络请求失败（curl {metadata['curl_code']}）")
            try:
                raw, status = output.rsplit(b"\n", 1)
                status = int(status)
            except (ValueError, TypeError):
                raise RuntimeError("微信响应无法解析") from None
            metadata["http_status"] = status
            if not 200 <= status < 300:
                raise RuntimeError(f"微信 HTTP {status}")
            try:
                result = json.loads(raw)
                if not isinstance(result, dict):
                    raise ValueError()
            except (ValueError, TypeError):
                raise RuntimeError("微信响应无法解析") from None
            if result.get("ret", 0) != 0 or result.get("errcode", 0) != 0:
                code = result.get("errcode") or result.get("ret")
                metadata["error_code"] = code
                metadata["api_error_kind"] = api_error_kind(
                    code, result.get("errmsg") or result.get("msg"))
                if auth and str(code) == "-14":
                    raise WeChatAuthExpired()
                raise RuntimeError(f"微信 API 错误 {code}")
            # Poll and typing successes are routine traffic; failures remain visible.
            ok, level = True, "debug" if route in (
                "ilink/bot/getupdates", "ilink/bot/getconfig", "ilink/bot/sendtyping") else "info"
            return result
        except WeChatPollTimeout:
            level = "debug"
            metadata.update(status="poll_idle_timeout", timeout_stage="request_or_response")
            raise
        except asyncio.CancelledError:
            level = "debug"
            metadata["status"] = "cancelled"
            raise
        except Exception as error:
            if metadata.get("curl_code") == 28 or isinstance(error, TimeoutError):
                metadata["timeout_stage"] = timeout_stage(metadata)
            metadata.update(error_fields(error))
            raise
        finally:
            elapsed = time.perf_counter() - started
            # One outcome per request; never include bodies, URLs or exception text.
            log_event("wechat.request", level=level, component="wechat", ok=ok,
                      duration_ms=elapsed * 1000, **metadata)
            if metadata.get("status") != "poll_idle_timeout" and (
                    metadata.get("curl_code") or (route != "ilink/bot/getupdates" and elapsed >= 2)):
                print(f"微信请求 {route}: curl={metadata.get('curl_code')} elapsed={elapsed:.2f}s",
                      file=sys.stderr, flush=True)

    async def poll(self, cursor):
        result = await self.request("ilink/bot/getupdates", {"get_updates_buf": cursor},
                                    timeout=self.poll_timeout + POLL_TIMEOUT_GRACE)
        suggested = result.get("longpolling_timeout_ms")
        if type(suggested) is int and suggested > 0:
            self.poll_timeout = min(MAX_POLL_TIMEOUT, max(1, suggested / 1000))
        return result

    async def send(self, entry):
        result = await self.request("ilink/bot/sendmessage", {"msg": {
            "from_user_id": "", "to_user_id": entry["user"],
            "client_id": entry["id"], "message_type": 2, "message_state": 2,
            "context_token": entry["context"],
            "item_list": [entry["item"]] if "item" in entry else
                         [{"type": 1, "text_item": {"text": entry["text"]}}]}},
            timeout=SEND_TIMEOUT, request_id=entry.get("requestId"))
        # A 200 alone does not prove delivery: record whether the server
        # acknowledged the message with an id, so a silent drop is visible.
        keys = result if isinstance(result, dict) else {}
        acknowledged = any(key in keys for key in (
            "msgid", "msg_id", "message_id", "messageId", "msgId"))
        log_event("wechat.send_ack", component="wechat",
                  status="acked" if acknowledged else "no_id", count=len(keys),
                  request_id=entry.get("requestId"))
        return result

    async def typing_ticket(self, user, context=None):
        body = {"ilink_user_id": user}
        if context:
            body["context_token"] = context
        result = await self.request("ilink/bot/getconfig", body, timeout=TYPING_TIMEOUT)
        ticket = result.get("typing_ticket")
        return ticket if isinstance(ticket, str) else ""

    async def set_typing(self, user, ticket, status):
        await self.request("ilink/bot/sendtyping", {
            "ilink_user_id": user,
            "typing_ticket": ticket,
            "status": status,
        }, timeout=TYPING_TIMEOUT)


def print_qr(url):
    from platform_ui import show_qr
    if not show_qr(url):
        print("未能绘制二维码，可使用二维码工具打开下面的链接。")


async def login(store, *, starting=False, rebind=False,
                before_identity_change=None, persist=True):
    """Acquire credentials; rebind defers local writes until its commit lock."""
    client = WeChat()
    try:
        return await _login(client, store, starting=starting, rebind=rebind,
                            before_identity_change=before_identity_change, persist=persist)
    finally:
        await client.close()


async def _login(client, store, *, starting, rebind, before_identity_change, persist):
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
                verification = WeChat(previous)
                try:
                    await verification.request("ilink/bot/getconfig",
                                               {"ilink_user_id": previous["user"]}, timeout=10)
                finally:
                    await verification.close()
            print("服务端确认此微信机器人已绑定；本地凭据保持不变。", flush=True)
            return LoginResult(previous, previous, False, already_connected=True)
        elif status in ("expired", "verify_code_blocked"):
            raise RuntimeError("二维码已过期或验证受限；请重新运行 login")
        await asyncio.sleep(1)
    raise RuntimeError("扫码等待超时，请重新运行 login")
