"""Offline boundaries for pooled CDN transfers; never contact WeChat."""
import asyncio
import inspect
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from curl_cffi.requests.exceptions import RequestException

from media import Media, MediaError
from storage import Store
from wechat_http import PooledHTTP

URL = "https://novac2c.cdn.weixin.qq.com/c2c/download?encrypted_query_param=SECRET"

_isolated = None


def setUpModule():
    # These fixtures drive real transfers, which log; keep them out of the bridge log.
    global _isolated
    import diagnostics
    _isolated = tempfile.TemporaryDirectory()
    diagnostics.configure(path=Path(_isolated.name) / "bridge.jsonl")


def tearDownModule():
    import diagnostics
    diagnostics.reset()
    _isolated.cleanup()


class FakeResponse:
    def __init__(self, status=200, headers=None, infos=None):
        self.status_code = status
        self.headers = headers or {}
        self.infos = infos or {}


class FakeSession:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []
        self.closed = 0
        self.curl_options = {}

    async def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        result = self.handler(method, url, kwargs)
        if inspect.isawaitable(result):
            result = await result
        return result

    async def close(self):
        self.closed += 1


class PooledTransferTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.sessions = []

    def install_session(self, handler):
        session = FakeSession(handler)
        self.sessions.append(session)
        patcher = patch("wechat_http._session", return_value=session)
        patcher.start()
        self.addCleanup(patcher.stop)
        return session

    async def test_get_uses_outgoing_pool_no_auth_and_returns_parameter(self):
        def handler(method, url, kwargs):
            self.assertEqual(method, "GET")
            self.assertEqual(kwargs["headers"], {})
            self.assertIsNone(kwargs["data"])
            self.assertEqual(kwargs["timeout"], (10, 50))
            self.assertIs(kwargs["accept_encoding"], None)
            kwargs["content_callback"](b"body")
            return FakeResponse(headers={"x-encrypted-param": "parameter"})

        session = self.install_session(handler)
        transport = PooledHTTP()
        try:
            body, status, code, parameter = await transport.transfer(URL, None, limit=1024)
        finally:
            await transport.close()
        self.assertEqual((body, status, code, parameter),
                         (b"body", 200, 0, "parameter"))
        self.assertEqual(len(session.calls), 1)
        self.assertNotIn("Authorization", session.calls[0][2]["headers"])

    async def test_post_sends_bytes_without_api_credentials(self):
        def handler(method, url, kwargs):
            self.assertEqual(method, "POST")
            self.assertEqual(kwargs["headers"], {"Content-Type": "application/octet-stream"})
            self.assertEqual(kwargs["data"], b"ciphertext")
            kwargs["content_callback"](b"")
            return FakeResponse()

        session = self.install_session(handler)
        transport = PooledHTTP()
        try:
            await transport.transfer(URL, b"ciphertext", limit=1024)
        finally:
            await transport.close()
        self.assertEqual(len(session.calls), 1)
        self.assertNotIn("Authorization", session.calls[0][2]["headers"])

    async def test_attachment_limit_allows_more_than_the_api_cap(self):
        payload = b"x" * (16 * 1024 * 1024 + 1)
        def handler(method, url, kwargs):
            self.assertEqual(kwargs["content_callback"](payload), len(payload))
            return FakeResponse()

        session = self.install_session(handler)
        transport = PooledHTTP()
        try:
            body, status, code, parameter = await transport.transfer(
                URL, None, limit=20 * 1024 * 1024 + 16)
        finally:
            await transport.close()
        self.assertEqual((len(body), status, code, parameter),
                         (len(payload), 200, 0, None))
        self.assertEqual(len(session.calls), 1)

    async def test_over_limit_aborts_callback_with_curl_63(self):
        def handler(method, url, kwargs):
            self.assertEqual(kwargs["content_callback"](b"12345"), 0xFFFFFFFF)
            raise RequestException("private URL", 23, FakeResponse())

        self.install_session(handler)
        transport = PooledHTTP()
        try:
            body, status, code, parameter = await transport.transfer(URL, None, limit=4)
        finally:
            await transport.close()
        self.assertEqual((body, status, code, parameter), (b"", 200, 63, None))

    async def test_requests_reuse_one_session_and_close_releases_it(self):
        def handler(method, url, kwargs):
            kwargs["content_callback"](b"")
            return FakeResponse()

        session = self.install_session(handler)
        transport = PooledHTTP()
        await transport.transfer(URL, None, limit=1024)
        await transport.transfer(URL, None, limit=1024)
        self.assertEqual(len(session.calls), 2)
        await transport.close()
        self.assertEqual(session.closed, 1)

    async def test_discarding_the_outgoing_pool_forces_new_connections(self):
        created = []

        def factory(*_args, **_kwargs):
            session = FakeSession(lambda method, url, kwargs: FakeResponse())
            created.append(session)
            return session

        with patch("wechat_http._session", side_effect=factory):
            transport = PooledHTTP()
            try:
                await transport.transfer(URL, None, limit=1024)
                self.assertEqual(len(created), 1)
                await transport.discard_outgoing()
                self.assertEqual(created[0].closed, 1)
                await transport.transfer(URL, None, limit=1024)
                self.assertEqual(len(created), 2)
            finally:
                await transport.close()
        self.assertEqual(len(created[1].calls), 1)

    async def test_discard_does_not_close_a_session_with_active_requests(self):
        started = asyncio.Event()
        release = asyncio.Event()
        created = []

        async def first_handler(method, url, kwargs):
            started.set()
            await release.wait()
            kwargs["content_callback"](b"first")
            return FakeResponse()

        def factory(*_args, **_kwargs):
            handler = first_handler if not created else (
                lambda method, url, kwargs: FakeResponse())
            session = FakeSession(handler)
            created.append(session)
            return session

        with patch("wechat_http._session", side_effect=factory):
            transport = PooledHTTP()
            first = asyncio.create_task(transport.transfer(URL, None, limit=1024))
            await started.wait()
            await transport.discard_outgoing()
            self.assertEqual(created[0].closed, 0)
            second = await transport.transfer(URL, None, limit=1024)
            self.assertEqual(len(created), 2)
            release.set()
            self.assertEqual((await first)[0], b"first")
            await asyncio.sleep(0)
            self.assertEqual(created[0].closed, 1)
            self.assertEqual(second[0], b"")
            await transport.close()


class MediaBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "state")

    async def asyncTearDown(self):
        self.store.lock.close()
        self.temp.cleanup()

    def media(self, http):
        channel = SimpleNamespace(http=http, request=AsyncMock(return_value={
            "upload_full_url": URL,
        }))
        return Media(self.store, channel), channel

    async def test_http_status_and_curl_failures_are_generic(self):
        media, channel = self.media(SimpleNamespace(
            transfer=AsyncMock(return_value=(b"", 500, 0, None))))
        with self.assertRaisesRegex(MediaError, "HTTP 200"):
            await media.transfer(URL)
        channel.http.transfer.return_value = (b"", 0, 28, None)
        with self.assertRaisesRegex(MediaError, "curl 28"):
            await media.transfer(URL)

    async def test_failures_are_logged_without_the_signed_url(self):
        records = []
        media, channel = self.media(SimpleNamespace(
            transfer=AsyncMock(return_value=(b"", 0, 28, None))))
        with patch("media.log_event", side_effect=lambda name, **fields: records.append((name, fields))):
            with self.assertRaises(MediaError):
                await media.transfer(URL)
        self.assertEqual(records[0][0], "media.transfer_failed")
        self.assertEqual(records[0][1]["kind"], "curl")
        self.assertEqual(records[0][1]["curl_code"], 28)
        self.assertNotIn("SECRET", str(records))

    async def test_one_transient_stall_is_retried(self):
        media, channel = self.media(SimpleNamespace(transfer=AsyncMock(side_effect=[
            (b"", 0, 28, None), (b"body", 200, 0, "parameter")])))
        with patch("media.log_event"):
            received, parameter = await media.transfer(URL)
        self.assertEqual((received, parameter), (b"body", "parameter"))
        self.assertEqual(channel.http.transfer.await_count, 2)

    async def test_an_upload_stall_is_not_retried_on_the_same_filekey(self):
        media, channel = self.media(SimpleNamespace(transfer=AsyncMock(side_effect=[
            (b"", 0, 28, None), (b"", 200, 0, "parameter")])))
        with patch("media.log_event"):
            with self.assertRaisesRegex(MediaError, "curl 28"):
                await media.transfer(URL, b"ciphertext")
        self.assertEqual(channel.http.transfer.await_count, 1)

    async def test_a_failed_transfer_drops_the_connection_before_retrying(self):
        order = []
        responses = [(b"", 0, 28, None), (b"body", 200, 0, "p")]

        def transfer(*_args, **_kwargs):
            order.append("transfer")
            return responses.pop(0)

        async def discard():
            order.append("discard")

        http = SimpleNamespace(transfer=AsyncMock(side_effect=transfer),
                               discard_outgoing=discard)
        media, _channel = self.media(http)
        with patch("media.log_event"):
            await media.transfer(URL)
        self.assertEqual(order, ["transfer", "discard", "transfer"])

    async def test_size_limit_and_server_errors_are_not_retried(self):
        media, channel = self.media(SimpleNamespace(
            transfer=AsyncMock(return_value=(b"", 0, 63, None))))
        with patch("media.log_event"):
            with self.assertRaisesRegex(MediaError, "大小限制"):
                await media.transfer(URL)
        self.assertEqual(channel.http.transfer.await_count, 1)
        channel.http.transfer.reset_mock()
        channel.http.transfer.return_value = (b"", 500, 0, None)
        with patch("media.log_event"):
            with self.assertRaisesRegex(MediaError, "HTTP 200"):
                await media.transfer(URL)
        self.assertEqual(channel.http.transfer.await_count, 1)

    async def test_timeout_and_cancel_are_distinguished(self):
        media, channel = self.media(SimpleNamespace(
            transfer=AsyncMock(side_effect=TimeoutError())))
        with self.assertRaisesRegex(MediaError, "总超时"):
            await media.transfer(URL)
        channel.http.transfer.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await media.transfer(URL)

    async def test_transport_error_does_not_expose_the_signed_url(self):
        media, _channel = self.media(SimpleNamespace(
            transfer=AsyncMock(side_effect=RuntimeError(
                "failed " + URL))))
        with self.assertRaises(MediaError) as raised:
            await media.transfer(URL)
        self.assertNotIn("SECRET", str(raised.exception))
        self.assertNotIn("encrypted_query_param", str(raised.exception))

    async def test_download_returns_parameter_and_upload_uses_no_auth_headers(self):
        http = SimpleNamespace(transfer=AsyncMock(return_value=(
            b"plain", 200, 0, "download-parameter")))
        media, _channel = self.media(http)
        self.assertEqual(await media.transfer(URL), (b"plain", "download-parameter"))
        self.assertEqual(http.transfer.await_args.kwargs["limit"], media.limit + 16)

        source = Path(self.temp.name) / "asset.bin"
        source.write_bytes(b"asset")
        with patch("media.cipher", return_value=b"ciphertext"):
            item = await media.upload({
                "asset": {"path": str(source), "name": "asset.bin", "kind": "file"},
                "user": "owner",
            })
        self.assertEqual(item["file_item"]["media"]["encrypt_query_param"],
                         "download-parameter")
        self.assertEqual(http.transfer.await_args.args[1], b"ciphertext")


if __name__ == "__main__":
    unittest.main()
