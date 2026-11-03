"""iLink image/file CDN transport. Cipher format is prescribed by Tencent."""
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import time
import urllib.parse
import uuid

from configuration import DEFAULT_SOURCE_HINT
from diagnostics import error_fields, log_event
from platform_support import IS_WINDOWS

CDN_BASE = "https://novac2c.cdn.weixin.qq.com/c2c"
DEFAULT_MAX_MIB = 20
# A single stall should not fail the whole attachment; the download is a GET,
# so one retry is safe. Six seconds already exceeds a normal CDN connect by
# an order of magnitude on this link.
CDN_ATTEMPTS = 2
CDN_CONNECT_TIMEOUT = 6
MAX_ATTACHMENTS = 10
OUTPUT_HINT = "\n\n" + DEFAULT_SOURCE_HINT
VIDEO_SUFFIXES = {
    ".3g2", ".3gp", ".avi", ".flv", ".m2ts", ".m4v", ".mkv", ".mov",
    ".mp4", ".mpeg", ".mpg", ".ogv", ".webm",
}
VIDEO_BRANDS = {
    b"3g2a", b"3gp4", b"3gp5", b"M4V ", b"avc1", b"iso2", b"isom",
    b"mp41", b"mp42", b"qt  ",
}


class MediaError(ValueError):
    pass


def image_extension(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    return None


def video_extension(name, data):
    suffix = Path(name).suffix.lower()
    if suffix in VIDEO_SUFFIXES:
        return suffix
    if len(data) >= 12 and data[4:8] == b"ftyp" and data[8:12] in VIDEO_BRANDS:
        return ".mp4"
    if data.startswith((b"\x1aE\xdf\xa3", b"FLV", b"\x00\x00\x01\xba")):
        return ".webm" if data.startswith(b"\x1aE\xdf\xa3") else ".mp4"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"AVI ":
        return ".avi"
    return None


def cipher(data, key, *, decrypt=False):
    try:
        from cryptography.hazmat.primitives import padding
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError:
        raise MediaError("附件加解密需要 Python cryptography，请先安装该依赖。") from None
    try:
        engine = Cipher(algorithms.AES128(key), modes.ECB())
        if decrypt:
            context = engine.decryptor()
            padded = context.update(data) + context.finalize()
            unpad = padding.PKCS7(128).unpadder()
            return unpad.update(padded) + unpad.finalize()
        pad = padding.PKCS7(128).padder()
        padded = pad.update(data) + pad.finalize()
        context = engine.encryptor()
        return context.update(padded) + context.finalize()
    except ValueError:
        raise MediaError("附件密钥、密文或填充无效。") from None


def decode_key(media, hex_key=None):
    try:
        if hex_key:
            key = bytes.fromhex(hex_key)
        else:
            key = base64.b64decode(media["aes_key"], validate=True)
            if len(key) == 32:
                key = bytes.fromhex(key.decode("ascii"))
        if len(key) != 16:
            raise ValueError
        return key
    except (ValueError, KeyError, UnicodeError, TypeError):
        raise MediaError("附件缺少有效的 AES-128 密钥。") from None


def cdn_url(value):
    try:
        url = urllib.parse.urlsplit(value)
        if (url.scheme != "https" or url.hostname != "novac2c.cdn.weixin.qq.com"
                or url.username or url.password or url.port not in (None, 443)
                or url.fragment):
            raise ValueError
    except (TypeError, ValueError):
        raise MediaError("附件 CDN 地址不在已核对的微信域名内。") from None
    return value


def split_answer(answer):
    """Only standalone markers outside code fences can request attachment delivery."""
    lines, attachments, fenced = [], [], False
    for line in answer.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        match = re.fullmatch(r"WECHAT_MEDIA:\s*(\{.*\})\s*", line)
        if match and not fenced:
            try:
                spec = json.loads(match[1])
                if (not isinstance(spec.get("path"), str)
                        or spec.get("kind", "auto") not in ("auto", "image", "video", "file")):
                    raise ValueError
                attachments.append(spec)
                continue
            except (ValueError, TypeError):
                pass
        lines.append(line)
    return "\n".join(lines).strip(), attachments


class Media:
    def __init__(self, store, channel, config=None):
        self.root = (store.root / "media").resolve()
        self.channel = channel
        self.limit = int((config or {}).get("maxMiB", DEFAULT_MAX_MIB)) * 1024 * 1024
        if not 0 < self.limit <= 100 * 1024 * 1024:
            raise MediaError("media.maxMiB 必须在 1 到 100 之间。")

    def write(self, folder, name, data):
        directory = self.root / folder
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not IS_WINDOWS:
            directory.chmod(0o700)
            self.root.chmod(0o700)
        name = re.sub(r'[\x00-\x1f/\\<>:"|?*]', "_", name)
        # Budget UTF-8 bytes as well as Windows code units; leave room for UUID.
        name = name.encode("utf-8")[:180].decode("utf-8", errors="ignore").rstrip(". ") or "attachment.bin"
        path = directory / (uuid.uuid4().hex + "-" + name)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        return path

    async def transfer(self, url, data=None):
        url = cdn_url(url)
        for attempt in range(1, CDN_ATTEMPTS + 1):
            retry = attempt < CDN_ATTEMPTS
            started = time.monotonic()
            try:
                received, status, code, parameter = await self.channel.http.transfer(
                    url, data, limit=self.limit + 16,
                    connect_timeout=CDN_CONNECT_TIMEOUT)
            except TimeoutError as error:
                self._log_transfer(False, started, kind="timeout", error=error)
                if retry:
                    self._log_retry(attempt, "timeout")
                    continue
                raise MediaError("附件传输达到总超时限制。") from None
            except Exception as error:
                # Transport exception strings can contain the encrypted CDN query.
                self._log_transfer(False, started, kind="transport", error=error)
                if retry:
                    self._log_retry(attempt, "transport")
                    continue
                raise MediaError("附件 CDN 请求失败，未输出含令牌的 URL。") from None
            if code:
                if code == 63:
                    self._log_transfer(False, started, kind="size_limit", curl_code=code)
                    raise MediaError("附件超过本地大小限制。")
                self._log_transfer(False, started, kind="curl", curl_code=code)
                if code == 28 and retry:
                    self._log_retry(attempt, "curl")
                    continue
                raise MediaError(f"附件 CDN 请求失败（curl {code}），未输出含令牌的 URL。")
            if status != 200:
                self._log_transfer(False, started, kind="http_status", http_status=status)
                raise MediaError("微信 CDN 未返回 HTTP 200。")
            self._log_transfer(True, started, kind="ok", http_status=status,
                               bytes_=len(received))
            return received, parameter

    @staticmethod
    def _log_retry(attempt, kind):
        log_event("media.transfer_retry", level="warning", component="media",
                  attempt=attempt, kind=kind)

    @staticmethod
    def _log_transfer(ok, started, *, kind, error=None, curl_code=None,
                      http_status=None, bytes_=None):
        fields = {"component": "media", "ok": ok, "kind": kind,
                  "duration_ms": (time.monotonic() - started) * 1000}
        if curl_code is not None:
            fields["curl_code"] = curl_code
        if http_status is not None:
            fields["http_status"] = http_status
        if bytes_ is not None:
            fields["bytes"] = bytes_
        if error is not None:
            fields.update(error_fields(error))
        log_event("media.transfer" if ok else "media.transfer_failed",
                  level="info" if ok else "warning", **fields)

    async def incoming(self, items):
        if len(items) > MAX_ATTACHMENTS:
            raise MediaError(f"每条消息最多 {MAX_ATTACHMENTS} 个附件。")
        result = []
        for item in items:
            item_type = item.get("type")
            field = {2: "image_item", 4: "file_item", 5: "video_item"}.get(item_type)
            if field is None:
                raise MediaError("收到不支持的附件类型。")
            is_image = item_type == 2
            is_video = item_type == 5
            payload = item.get(field, {})
            reference = payload.get("media", {})
            url = reference.get("full_url")
            if not url:
                param = reference.get("encrypt_query_param")
                if not param:
                    raise MediaError("附件缺少下载引用。")
                url = CDN_BASE + "/download?" + urllib.parse.urlencode({"encrypted_query_param": param})
            data, _ = await self.transfer(url)
            if payload.get("aeskey") or reference.get("aes_key"):
                key = decode_key(reference, payload.get("aeskey") if is_image else None)
                data = await asyncio.to_thread(cipher, data, key, decrypt=True)
            elif not is_image:
                raise MediaError("附件缺少解密密钥。")
            if len(data) > self.limit:
                raise MediaError("附件超过本地大小限制。")
            digest = payload.get("md5") or payload.get("video_md5")
            if digest and (not isinstance(digest, str) or hashlib.md5(data).hexdigest().lower() != digest.lower()):
                raise MediaError("附件 MD5 校验失败。")
            kind = ("image" if is_image and image_extension(data) else
                    "video" if is_video or video_extension("video", data) else "file")
            default_name = ("image" + (image_extension(data) or ".bin") if is_image else
                            "video" + (video_extension("video", data) or ".mp4") if is_video else
                            "file.bin")
            name = str(payload.get("file_name") or default_name)
            path = await asyncio.to_thread(self.write, "incoming", name, data)
            result.append({"path": str(path), "kind": kind})
        return result

    def snapshot(self, value, cwd, kind="auto", *, automatic=False):
        if kind not in ("auto", "image", "video", "file"):
            raise MediaError("未知附件类型。")
        path = Path(value).expanduser()
        if IS_WINDOWS and (path.drive.startswith("\\\\") or
                (path.drive and not path.is_absolute()) or
                any(":" in part for part in path.parts if part != path.anchor)):
            raise MediaError("不支持网络共享、设备路径、驱动器相对路径或 NTFS 附加数据流。")
        if not path.is_absolute():
            path = Path(cwd) / path
        try:
            path = path.resolve(strict=True)
            # The bridge's account token/state must not become an accidental attachment.
            state_root = self.root.parent
            if path.is_relative_to(state_root) and not path.is_relative_to(self.root):
                raise MediaError("不能发送微信桥的登录凭据或运行状态。")
            if automatic and not path.is_relative_to(self.root):
                workspace = Path(cwd).expanduser().resolve()
                if (workspace in (Path.home(), Path(workspace.anchor)) or not path.is_relative_to(workspace)
                        or any(part.startswith(".") for part in path.relative_to(workspace).parts)):
                    raise MediaError("自动回传仅限当前工作目录内的非隐藏文件或媒体目录；其他路径请明确使用 /send。")
                if IS_WINDOWS:
                    import stat
                    for parent in (path, *path.parents):
                        if parent == workspace:
                            break
                        if parent.stat().st_file_attributes & (stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_SYSTEM):
                            raise MediaError("自动回传不允许隐藏或系统文件；请明确使用 /send。")
            if not path.is_file():
                raise MediaError("附件路径不是普通文件。")
            with path.open("rb") as stream:
                data = stream.read(self.limit + 1)
        except OSError:
            raise MediaError("附件不存在或无法读取。") from None
        if len(data) > self.limit:
            raise MediaError("附件超过本地大小限制。")
        detected = image_extension(data)
        if kind == "image" and not detected:
            raise MediaError("图片仅支持 PNG、JPEG、GIF、WebP；其他格式请作为文件发送。")
        detected_video = video_extension(path.name, data)
        if kind == "video" and not detected_video:
            raise MediaError("视频格式无法识别；请使用 /sendfile 作为文件发送。")
        selected = ("image" if detected and kind != "file" else
                    "video" if detected_video and kind != "file" else "file")
        frozen = self.write("outgoing", path.name, data)
        return {"path": str(frozen), "name": path.name, "kind": selected}

    async def upload(self, entry):
        asset = entry["asset"]
        try:
            with Path(asset["path"]).open("rb") as stream:
                data = stream.read(self.limit + 1)
        except OSError:
            raise MediaError("待发附件快照已丢失或无法读取。") from None
        if len(data) > self.limit:
            raise MediaError("待发附件超过大小限制。")
        key = secrets.token_bytes(16)
        encrypted = await asyncio.to_thread(cipher, data, key)
        filekey = secrets.token_hex(16)
        kind = asset["kind"]
        image = kind == "image"
        video = kind == "video"
        response = await self.channel.request("ilink/bot/getuploadurl", {
            "filekey": filekey, "media_type": 1 if image else 2 if video else 3,
            "to_user_id": entry["user"], "rawsize": len(data),
            "rawfilemd5": hashlib.md5(data).hexdigest(), "filesize": len(encrypted),
            "no_need_thumb": True, "aeskey": key.hex()})
        url = response.get("upload_full_url")
        if not url:
            param = response.get("upload_param")
            if not param:
                raise MediaError("微信未返回附件上传地址。")
            url = CDN_BASE + "/upload?" + urllib.parse.urlencode({
                "encrypted_query_param": param, "filekey": filekey})
        _, download = await self.transfer(url, encrypted)
        if not download:
            raise MediaError("CDN 上传未返回下载参数。")
        reference = {"encrypt_query_param": download,
                     "aes_key": base64.b64encode(key.hex().encode()).decode(), "encrypt_type": 1}
        if image:
            return {"type": 2, "image_item": {"media": reference, "mid_size": len(encrypted)}}
        if video:
            return {"type": 5, "video_item": {"media": reference, "video_size": len(encrypted)}}
        return {"type": 4, "file_item": {"media": reference,
                                         "file_name": asset["name"], "len": str(len(data))}}
