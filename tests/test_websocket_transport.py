"""Offline ownership and endpoint contracts; no daemon or model calls."""
import asyncio
import json
import unittest
from unittest.mock import AsyncMock, Mock, patch
from codex_transport import CodexTransport

class WebSocketTransportTests(unittest.IsolatedAsyncioTestCase):
    def transport(self, url="ws://127.0.0.1:45231"):
        return CodexTransport({"websocketUrl": url}, ".", on_request=AsyncMock(),
            on_event=Mock(), on_connect=Mock(), on_disconnect=Mock())

    async def test_reject_nonlocal_and_malformed_addresses(self):
        for url in ("ws://example.com:80", "wss://127.0.0.1:80", "ws://127.0.0.1", 
                    "ws://127.0.0.1:65536", "ws://user@127.0.0.1:80", 4):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.transport(url)

    async def test_external_connection_does_not_require_local_cli(self):
        transport = self.transport()
        transport.connect = AsyncMock()
        with patch("backend_runtime.resolve_command", side_effect=AssertionError("spawned CLI")):
            await transport.start()
        transport.connect.assert_awaited_once()
        self.assertTrue(transport.shared)
        self.assertIsNone(transport.proc)

    async def test_send_without_child_and_close_only_our_connection(self):
        transport = self.transport()
        end = asyncio.Event()
        transport.websocket = Mock(send=AsyncMock(), close=AsyncMock(side_effect=end.set))
        socket = transport.websocket
        transport.reader = asyncio.create_task(end.wait())
        self.assertTrue(transport.connected)
        await transport.write({"method": "initialized", "params": {}})
        self.assertEqual(json.loads(socket.send.call_args.args[0])["method"], "initialized")
        await transport.close()
        socket.close.assert_awaited_once()
        self.assertIsNone(transport.proc)
        self.assertFalse(transport.connected)

    async def test_failed_handshake_is_closed_before_retry(self):
        transport = self.transport()
        transport.connect = AsyncMock(side_effect=OSError("connection refused"))
        transport.close = AsyncMock()
        with self.assertRaises(OSError):
            await transport.start()
        transport.close.assert_awaited_once()
