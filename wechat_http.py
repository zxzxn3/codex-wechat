"""Service-owned libcurl pools; no request retries or credential-bearing logs."""
import asyncio
import json
import os
import ssl

from configuration import DEFAULT_CONNECTION_IDLE_SECONDS
from diagnostics import error_fields, log_event
from latency import CURL_TIMING_FIELDS, curl_timings

MAX_RESPONSE_BYTES = 16 * 1024 * 1024


def _max_idle_age(seconds):
    # MAXAGE_CONN has no unlimited sentinel: zero forbids aged reuse.
    # Use the portable signed-long ceiling when application expiry is disabled.
    return seconds or (2**31 - 1)


def _session(idle_seconds=DEFAULT_CONNECTION_IDLE_SECONDS):
    # Lazy import keeps standalone installer/help usable before dependencies exist.
    from curl_cffi import CurlInfo, CurlOpt
    from curl_cffi.requests import AsyncSession
    infos = [getattr(CurlInfo, name.removeprefix("time_").upper() + "_TIME")
             for name in CURL_TIMING_FIELDS]
    return AsyncSession(
        max_clients=8, allow_redirects=False, trust_env=True,
        default_headers=False, discard_cookies=True,
        verify=(os.environ.get("CURL_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
                or ssl.get_default_verify_paths().cafile or True),
        curl_options={CurlOpt.MAXAGE_CONN: _max_idle_age(idle_seconds),
                      CurlOpt.PROTOCOLS_STR: b"https"},
        curl_infos=infos + [CurlInfo.USED_PROXY, CurlInfo.NUM_CONNECTS])


def _metrics(response):
    if response is None:
        return {}
    from curl_cffi import CurlInfo
    raw = {name: response.infos.get(getattr(
        CurlInfo, name.removeprefix("time_").upper() + "_TIME"))
           for name in CURL_TIMING_FIELDS}
    reused = (response.infos.get(CurlInfo.NUM_CONNECTS) == 0
              and (raw.get("time_pretransfer") or 0) > 0)
    values = curl_timings(json.dumps(raw).encode(), connection_reused=reused)
    values["connection_reused"] = reused
    proxy = response.infos.get(CurlInfo.USED_PROXY)
    if proxy in (0, 1):
        values["proxy_used"] = bool(proxy)
    return values


class _Pool:
    def __init__(self, idle_seconds=DEFAULT_CONNECTION_IDLE_SECONDS):
        self.session = None
        self.active = 0
        self.idle = None
        self.tasks = set()
        self.closed = False
        self.idle_seconds = idle_seconds
        self.idle_since = None

    def set_idle_seconds(self, seconds):
        if seconds == self.idle_seconds or self.closed:
            return
        self.idle_seconds = seconds
        if self.session is not None:
            from curl_cffi import CurlOpt
            # Applied to new transfers; already-issued requests keep their options.
            self.session.curl_options[CurlOpt.MAXAGE_CONN] = _max_idle_age(seconds)
        if self.idle is not None:
            self.idle.cancel()
            self.idle = None
        if (seconds and not self.active and self.session is not None
                and self.idle_since is not None):
            elapsed = asyncio.get_running_loop().time() - self.idle_since
            self._schedule_expiry(max(0, seconds - elapsed))

    def acquire(self):
        if self.closed:
            raise RuntimeError("微信连接池已关闭")
        if self.idle is not None:
            self.idle.cancel()
            self.idle = None
        if self.session is None:
            self.session = _session(self.idle_seconds)
        self.active += 1
        return self.session

    def release(self):
        self.active -= 1
        if not self.active and not self.closed:
            self.idle_since = asyncio.get_running_loop().time()
            if self.idle_seconds:
                self._schedule_expiry(self.idle_seconds)

    def _schedule_expiry(self, delay):
        self.idle = asyncio.create_task(self._expire(delay))
        self.tasks.add(self.idle)
        self.idle.add_done_callback(self._idle_done)

    def _idle_done(self, task):
        self.tasks.discard(task)
        if not task.cancelled() and (error := task.exception()) is not None:
            log_event("wechat.pool_close_failed", level="warning", component="wechat",
                      **error_fields(error))

    async def _expire(self, delay):
        await asyncio.sleep(delay)
        # Detach before awaiting close: a new request gets a fresh session.
        session, self.session = self.session, None
        self.idle = None
        if session is not None:
            await session.close()

    async def close(self):
        self.closed = True
        idle, self.idle = self.idle, None
        if idle is not None:
            idle.cancel()
            await asyncio.gather(idle, return_exceptions=True)
        # An expiry that detached its session may still be closing it.
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
        session, self.session = self.session, None
        if session is not None:
            await session.close()


class PooledHTTP:
    def __init__(self, idle_seconds=DEFAULT_CONNECTION_IDLE_SECONDS):
        # Long polling cannot keep the outgoing pool alive or occupy its slots.
        self.poll = _Pool(idle_seconds)
        self.outgoing = _Pool(idle_seconds)

    def set_idle_seconds(self, seconds):
        self.poll.set_idle_seconds(seconds)
        self.outgoing.set_idle_seconds(seconds)

    async def request(self, url, headers, payload, *, timeout, polling=False):
        body, status, metrics, _ = await self._exchange(
            url, headers, payload, timeout=timeout, polling=polling)
        return body + b"\n" + str(status).encode(), metrics

    async def transfer(self, url, data, *, limit):
        # CDN requests use the existing pool, but never API authentication headers.
        headers = {"Content-Type": "application/octet-stream"} if data is not None else {}
        body, status, metrics, response_headers = await self._exchange(
            url, headers, data, timeout=60, limit=limit)
        return body, status, metrics["curl_code"], response_headers.get("x-encrypted-param")

    async def _exchange(self, url, headers, payload, *, timeout, polling=False,
                        limit=None):
        limit = MAX_RESPONSE_BYTES if limit is None else limit
        from curl_cffi.requests.exceptions import RequestException
        pool = self.poll if polling else self.outgoing
        session = pool.acquire()
        body = bytearray()
        oversized = False

        def consume(chunk):
            nonlocal oversized
            if len(body) + len(chunk) > limit:
                oversized = True
                # curl_cffi ignores short counts; use libcurl's explicit abort value.
                return 0xFFFFFFFF
            body.extend(chunk)
            return len(chunk)

        try:
            try:
                # curl_cffi defines tuple timeout as connect + remaining total.
                connect = min(timeout, 10)
                async with asyncio.timeout(timeout + 2):
                    response = await session.request(
                        "POST" if payload is not None else "GET", url,
                        headers=headers, data=payload,
                        timeout=(connect, timeout - connect),
                        content_callback=consume, accept_encoding=None)
                code = 0
            except RequestException as error:
                # Never propagate libcurl exception text (it can contain the URL).
                response, code = error.response, int(error.code)
            metrics = _metrics(response)
            metrics["curl_code"] = code
            if oversized:
                metrics["curl_code"] = 63
            status = response.status_code if response is not None else 0
            headers = getattr(response, "headers", {}) if response is not None else {}
            return bytes(body), status, metrics, headers
        finally:
            pool.release()

    async def close(self):
        await asyncio.gather(self.poll.close(), self.outgoing.close())
