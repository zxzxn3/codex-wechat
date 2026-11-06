"""Transport lifecycle with synthetic byte streams, no real daemon."""
import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

from codex_wechat.codex_transport import CodexTransport, CodexRpcError


class TransportTests(unittest.IsolatedAsyncioTestCase):
    def transport(self):
        return CodexTransport({}, "/tmp", on_request=AsyncMock(), on_event=Mock(),
                              on_connect=Mock(), on_disconnect=Mock())

    async def test_disconnect_fails_pending_requests_and_calls_owner(self):
        transport = self.transport()
        transport.receive = AsyncMock(return_value=b"")
        pending = asyncio.get_running_loop().create_future()
        transport.requests[1] = pending
        await transport.read()
        with self.assertRaises(RuntimeError):
            await pending
        transport.on_disconnect.assert_called_once()

    async def test_handler_failure_is_logged_before_the_disconnect(self):
        transport = self.transport()
        transport.on_event = Mock(side_effect=ValueError("bad payload"))
        messages = iter([b'{"method": "event"}', b""])
        transport.receive = AsyncMock(side_effect=lambda: next(messages))
        records = []
        with patch("codex_wechat.codex_transport.log_event",
                   side_effect=lambda name, **fields: records.append((name, fields))):
            await transport.read()
        self.assertEqual(records[0][0], "codex.read_failed")
        self.assertEqual(records[0][1]["error_type"], "ValueError")
        transport.on_disconnect.assert_called_once()

    async def test_rpc_pairs_response_and_keeps_error_classification(self):
        transport = self.transport()
        async def write(request):
            transport.requests[request["id"]].set_exception(CodexRpcError(-32603, "internal"))
        transport.write = write
        with self.assertRaises(CodexRpcError) as caught:
            await transport.rpc("turn/start", {})
        self.assertFalse(caught.exception.definite_rejection)
        self.assertIn("turn/start", str(caught.exception))
        self.assertEqual(transport.requests, {})

    async def test_close_only_terminates_its_proxy(self):
        transport = self.transport()
        process = Mock(returncode=None)
        process.communicate = AsyncMock(return_value=(b"", b""))
        process.wait = AsyncMock(return_value=0)
        process.stdin = None
        transport.proc = process
        transport.websocket = Mock(close=AsyncMock())
        await transport.close()
        process.terminate.assert_called()
        process.kill.assert_not_called()
        process.wait.assert_awaited_once()
