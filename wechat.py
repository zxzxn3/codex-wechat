"""iLink message adapter; see docs/SOURCES.md for protocol provenance."""
import asyncio
import base64
import json
import secrets
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "https://ilinkai.weixin.qq.com"
VERSION = "0.1.0"


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

    async def request(self, endpoint, body=None, *, auth=True, timeout=40):
        def perform():
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
                    payload["base_info"] = {"channel_version": VERSION,
                                            "bot_agent": "AgentWechat/" + VERSION}
                payload = json.dumps(payload).encode()
            req = urllib.request.Request(self.base + "/" + endpoint,
                                         data=payload, headers=headers)
            try:
                with self.opener.open(req, timeout=timeout) as response:
                    result = json.load(response)
            except urllib.error.HTTPError as error:
                raise RuntimeError(f"微信 HTTP {error.code}") from None
            except (OSError, ValueError):
                raise RuntimeError("微信网络请求失败或响应无法解析") from None
            if result.get("ret", 0) != 0 or result.get("errcode", 0) != 0:
                code = result.get("errcode") or result.get("ret")
                raise RuntimeError(f"微信 API 错误 {code}" +
                                   ("，请重新 login" if code == -14 else ""))
            return result
        return await asyncio.to_thread(perform)

    async def poll(self, cursor):
        return await self.request("ilink/bot/getupdates", {"get_updates_buf": cursor})

    async def send(self, entry):
        await self.request("ilink/bot/sendmessage", {"msg": {
            "from_user_id": "", "to_user_id": entry["user"],
            "client_id": entry["id"], "message_type": 2, "message_state": 2,
            "context_token": entry["context"],
            "item_list": [entry["item"]] if "item" in entry else
                         [{"type": 1, "text_item": {"text": entry["text"]}}]}})


async def login(store):
    client = WeChat()
    qr = await client.request("ilink/bot/get_bot_qrcode?bot_type=3",
                              {"local_token_list": []}, auth=False)
    url = qr["qrcode_img_content"]
    print("用手机微信扫描二维码并确认授权：", flush=True)
    try:
        subprocess.run(["qrencode", "-t", "ANSIUTF8", "--", url], check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("未能绘制二维码，可用 qrencode 或二维码工具打开下面的链接。")
    print(url, flush=True)
    deadline = time.monotonic() + 300
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
            previous = store.load("account")
            if previous and (previous["bot"], previous["user"]) != (account["bot"], account["user"]):
                raise RuntimeError("此状态目录已绑定另一个身份；请使用新的 --state-dir")
            store.save("account", account)
            print("绑定成功。只接受此次扫码用户的私聊；运行 start 启动桥接。")
            return
        if status == "scaned_but_redirect":
            host = data.get("redirect_host", "")
            client.base = trusted_url(host if host.startswith("https://") else "https://" + host)
        elif status == "need_verifycode":
            verify = await asyncio.to_thread(input, "请输入手机显示的配对码（仅本机）：")
        elif status in ("expired", "verify_code_blocked", "binded_redirect"):
            raise RuntimeError("二维码已过期、验证受限或已绑定；请重新运行 login")
        await asyncio.sleep(1)
    raise RuntimeError("扫码等待超时，请重新运行 login")
