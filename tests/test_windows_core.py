"""Portable core contracts; simulated Windows branches never run system tools."""
import asyncio
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from backend import CodexBackend
from codex_transport import CodexTransport
from pipe_websocket import PipeWebSocket
from profiles import definitions
from storage import Store
import platform_support
from websockets.server import ServerProtocol
from websockets.http11 import Request
from websockets.frames import Frame, Opcode


class PlatformTests(unittest.TestCase):
    def test_explicit_codex_path_is_not_replaced_by_path_installation(self):
        explicit = "/custom/codex.exe"
        with patch.object(platform_support, "IS_WINDOWS", True), \
                patch("platform_support.shutil.which", return_value=explicit) as which:
            self.assertEqual(platform_support.resolve_command([explicit, "app-server"], {}),
                             [explicit, "app-server"])
        which.assert_called_once_with(explicit, path=None)

    def test_windows_paths_and_optional_launcher(self):
        with patch.object(platform_support, "IS_WINDOWS", True), \
                patch.dict("os.environ", {"LOCALAPPDATA": "/tmp/local"}):
            self.assertEqual(platform_support.data_root(), Path("/tmp/local/codex-wechat"))
        with patch("profiles.IS_WINDOWS", True):
            self.assertEqual(set(definitions({})), {"codex"})
            self.assertEqual(set(definitions({"backends": {"deepseek": {
                "command": ["custom.exe", "app-server"]}}})), {"codex", "deepseek"})

    def test_windows_refuses_implicit_shell(self):
        with patch.object(platform_support, "IS_WINDOWS", True), \
                patch("platform_support.shutil.which", return_value="/tmp/codex.cmd"):
            with self.assertRaises(ValueError):
                platform_support.resolve_command(["codex", "app-server"], {})
        with patch.object(platform_support, "IS_WINDOWS", True), \
                patch("platform_support.shutil.which", return_value="/tmp/codex.exe"):
            self.assertEqual(platform_support.resolve_command(["codex", "app-server"], {}),
                             ["/tmp/codex.exe", "app-server"])

    def test_windows_store_uses_byte_lock_and_unicode(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch("storage.IS_WINDOWS", True), \
                patch("storage.msvcrt", SimpleNamespace(locking=Mock(), LK_NBLCK=2), create=True):
            store = Store(Path(directory) / "中文状态")
            try:
                store.save("bridge", {"text": "鲸鱼娘", "queue": []})
                self.assertEqual(store.load("bridge")["text"], "鲸鱼娘")
                self.assertEqual(store.lock.tell(), 0)
            finally:
                store.lock.close()

    @unittest.skipUnless(platform_support.IS_WINDOWS, "requires native Windows byte locking")
    def test_native_windows_second_instance_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Store(directory)
            try:
                with self.assertRaises(RuntimeError):
                    Store(directory)
            finally:
                first.lock.close()


class PipeWebSocketTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.server = ServerProtocol(max_size=16 * 1024 * 1024)
        self.stdout = asyncio.StreamReader()
        self.events = []
        self.buffered = bytearray()

        def write(data):
            self.server.receive_data(data)
            for event in self.server.events_received():
                if isinstance(event, Request):
                    self.server.send_response(self.server.accept(event))
                else:
                    self.events.append(event)
            for output in self.server.data_to_send():
                if output:
                    self.stdout.feed_data(output)

        self.process = SimpleNamespace(stdin=SimpleNamespace(write=write, drain=AsyncMock()),
                                       stdout=self.stdout)
        self.client = PipeWebSocket(self.process)
        await self.client.handshake()

    def flush_server(self):
        for output in self.server.data_to_send():
            if output:
                self.stdout.feed_data(output)

    async def test_handshake_json_and_fragmented_utf8_with_ping(self):
        await self.client.send('{"hello":"微信"}')
        self.assertEqual(self.events[-1].data.decode(), '{"hello":"微信"}')
        data = "鲸鱼娘".encode()
        self.server.send_text(data[:2], fin=False)
        self.server.send_ping(b"ping")
        self.server.send_continuation(data[2:], fin=True)
        self.flush_server()
        self.assertEqual(await self.client.recv(), "鲸鱼娘")
        self.assertTrue(any(isinstance(e, Frame) and e.opcode == Opcode.PONG for e in self.events))

    async def test_close_and_eof_do_not_wait_forever(self):
        await self.client.close()
        self.assertTrue(any(e.opcode == Opcode.CLOSE for e in self.events))
        with self.assertRaises(RuntimeError):
            await self.client.recv()

    async def test_abrupt_pipe_eof_reports_disconnect(self):
        self.stdout.feed_eof()
        with self.assertRaises(RuntimeError):
            await self.client.recv()


class WindowsTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_windows_defaults_to_stdio_and_preserves_explicit_shared_choice(self):
        callbacks = dict(on_request=AsyncMock(), on_event=Mock(), on_connect=Mock(),
                         on_disconnect=Mock())
        with patch("codex_transport.IS_WINDOWS", True):
            self.assertFalse(CodexTransport({}, "/tmp", **callbacks).shared)
            self.assertTrue(CodexTransport({"sharedDaemon": True}, "/tmp", **callbacks).shared)

    async def test_windows_probe_uses_only_launch_network_environment(self):
        backend = CodexBackend({}, "/tmp", lambda _: None)
        backend.transport.proc = SimpleNamespace(returncode=None)
        backend.transport.reader = SimpleNamespace(done=lambda: False)
        backend.transport.launch_env = {"HTTPS_PROXY": "http://localhost:1234"}
        backend.info = {"provider": "custom"}
        backend.rpc = AsyncMock(return_value={"config": {"model_providers": {
            "custom": {"base_url": "https://example.com/v1"}}}})
        with patch("backend.IS_WINDOWS", True), patch.dict("os.environ", {"API_KEY": "private"}):
            endpoint, env = await backend.probe_target()
        self.assertEqual(endpoint, "https://example.com/v1")
        self.assertEqual(env["HTTPS_PROXY"], "http://localhost:1234")
        self.assertNotIn("API_KEY", env)
