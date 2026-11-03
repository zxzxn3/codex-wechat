"""Offline request outcomes retain protocol, cancellation and privacy boundaries."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import diagnostics
from errors import WeChatAuthExpired
from wechat import WeChat
from tests.http_fixtures import exchange


class WeChatTransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "bridge.jsonl"
        diagnostics.configure(path=self.path, level="debug")
        self.client = WeChat({"token": "SECRET-TOKEN"})

    def tearDown(self):
        diagnostics.reset()
        self.temp.cleanup()

    def records(self):
        return [json.loads(line) for line in self.path.read_text().splitlines()]

    async def test_protocol_failures_emit_one_outcome_and_do_not_retry(self):
        cases = (
            (b'', 28, True, RuntimeError, "微信网络请求失败（curl 28）", None, None),
            (b'not json\n503', 0, True, RuntimeError, "微信 HTTP 503", 503, None),
            (b'not json\n200', 0, True, RuntimeError, "微信响应无法解析", 200, None),
            (b'[]\n200', 0, True, RuntimeError, "微信响应无法解析", 200, None),
            (b'{}', 0, True, RuntimeError, "微信响应无法解析", None, None),
            (b'{"errcode":-14}\n200', 0, True, WeChatAuthExpired, None, 200, "-14"),
            (b'{"errcode":-14}\n200', 0, False, RuntimeError, "微信 API 错误 -14", 200, "-14"),
            (b'{"ret":9}\n200', 0, True, RuntimeError, "微信 API 错误 9", 200, "9"),
        )
        for index, (output, code, auth, error, message, status, api_code) in enumerate(cases):
            with self.subTest(index=index):
                before = len(self.records()) if self.path.exists() else 0
                with exchange(output, code=code) as request:
                    with self.assertRaises(error) as caught:
                        await self.client.request("ilink/bot/getconfig", auth=auth,
                                                  request_id=f"req-{index}")
                    if message:
                        self.assertEqual(str(caught.exception), message)
                    request.assert_awaited_once()
                records = self.records()[before:]
                self.assertEqual(len(records), 1)
                self.assertFalse(records[0]["ok"])
                self.assertEqual(records[0]["request_id"], f"req-{index}")
                self.assertEqual(records[0].get("http_status"), status)
                self.assertEqual(records[0].get("error_code"), api_code)

    async def test_transport_failure_and_timeout_are_logged_without_exception_text(self):
        for error in (OSError("SECRET-PATH"), TimeoutError("SECRET-RESPONSE")):
            with self.subTest(error=type(error).__name__):
                before = len(self.records()) if self.path.exists() else 0
                with patch("wechat.PooledHTTP.request", AsyncMock(side_effect=error)):
                    with self.assertRaises(type(error)) as caught:
                        await self.client.request("ilink/bot/sendmessage", request_id="req-failure")
                    self.assertIs(caught.exception, error)
                records = self.records()[before:]
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0]["error_type"], type(error).__name__)
                self.assertNotIn("SECRET", json.dumps(records))

    async def test_cancellation_is_propagated_and_not_logged_as_warning(self):
        with patch("wechat.PooledHTTP.request", AsyncMock(side_effect=asyncio.CancelledError())):
            with self.assertRaises(asyncio.CancelledError):
                await self.client.request("ilink/bot/getupdates")
        records = self.records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["status"], "cancelled")
        self.assertEqual(records[0]["level"], "debug")

    async def test_success_preserves_timeout_auth_body_and_private_logs(self):
        with exchange() as request:
            result = await self.client.request("ilink/bot/sendmessage?secret=SECRET-QUERY",
                {"text": "SECRET-BODY"}, timeout=5, request_id="req-success")
        self.assertEqual(result, {"ret": 0})
        self.assertEqual(request.await_args.kwargs["timeout"], 5)
        self.assertFalse(request.await_args.kwargs["polling"])
        self.assertEqual(request.await_args.args[1]["Authorization"], "Bearer SECRET-TOKEN")
        self.assertIn(b"SECRET-BODY", request.await_args.args[2])
        records = self.records()
        self.assertEqual(len(records), 1)
        self.assertTrue(records[0]["ok"])
        self.assertEqual(records[0]["endpoint"], "ilink/bot/sendmessage")
        self.assertNotIn("SECRET", json.dumps(records))
