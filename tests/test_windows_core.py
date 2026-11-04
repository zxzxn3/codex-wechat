"""Portable core contracts; simulated Windows branches never run system tools."""
import asyncio
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from backend import (CodexBackend, TurnStalled, WRITER_BUSY, WRITER_FOLLOWED,
                     WRITER_NATIVE, WRITER_RELEASED)
import backend_runtime
from codex_transport import CodexTransport, CodexRpcError, SharedDaemonUnavailable
from errors import BackendStartupError
from pipe_websocket import PipeWebSocket
from profiles import definitions, resume_or_adopt
from storage import Store
import platform_support
from websockets.server import ServerProtocol
from websockets.http11 import Request
from websockets.frames import Frame, Opcode


class PlatformTests(unittest.TestCase):
    def test_existing_directory_is_tightened(self):
        import platform_support
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "state"
            target.mkdir(mode=0o755)
            target.chmod(0o755)
            platform_support.make_private(target)
            if not platform_support.IS_WINDOWS:
                self.assertEqual(target.stat().st_mode & 0o777, 0o700)

    def test_explicit_codex_path_is_not_replaced_by_path_installation(self):
        explicit = "/custom/codex.exe"
        with patch.object(platform_support, "IS_WINDOWS", True), \
                patch("platform_support.shutil.which", return_value=explicit) as which:
            self.assertEqual(platform_support.resolve_command([explicit, "app-server"], {}),
                             [explicit, "app-server"])
        which.assert_called_once_with(explicit, path=None)

    def test_windows_paths_and_optional_launcher(self):
        with patch.object(platform_support, "IS_WINDOWS", True), \
                patch.dict("os.environ", {"LOCALAPPDATA": "/tmp/local",
                                          "CODEX_WECHAT_DATA_ROOT": ""}):
            # An ambient override (a test-isolated run sets one) must not leak in.
            self.assertEqual(platform_support.data_root(), Path("/tmp/local/codex-wechat"))
        with patch.object(platform_support, "IS_WINDOWS", False), \
                patch.dict("os.environ", {"CODEX_WECHAT_DATA_ROOT": "/tmp/stable-data"}):
            self.assertEqual(platform_support.data_root(), Path("/tmp/stable-data").resolve())
        with patch("profiles.IS_WINDOWS", True):
            self.assertEqual(set(definitions({})), {"codex"})
            self.assertEqual(set(definitions({"backends": {"deepseek": {
                "command": ["custom.exe", "app-server"]}}})), {"deepseek"})

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


class DaemonLaunchTests(unittest.IsolatedAsyncioTestCase):
    """Starting the daemon from a launcher whose job forbids breakaway."""

    def runtime(self):
        return backend_runtime.BackendRuntime({}, "/cwd")

    def test_shared_error_preserves_cause_metadata(self):
        cause = BackendStartupError("DeepSeek 密钥环未就绪", retry_after=30)
        error = SharedDaemonUnavailable("共享后台不可用", cause=cause)
        self.assertEqual(error.cause_type, "BackendStartupError")
        self.assertEqual(error.retry_after, 30)

    def test_only_home_and_network_variables_cross_the_hop(self):
        line, env_file = backend_runtime.wmi_command_line(
            [r"C:\Program Files\codex.exe", "app-server", "daemon", "start"],
            {"HTTP_PROXY": "http://127.0.0.1:7897", "NO_PROXY": "127.0.0.1,localhost",
             "CODEX_HOME": r"C:\home", "PATH": r"C:\bin", "SECRET_TOKEN": "abc"},
            cwd=r"C:\cwd")
        try:
            self.assertIn("wmi_launch.py", line)
            self.assertIn(str(env_file), line)
            self.assertNotIn("HTTP_PROXY", line)
            self.assertNotIn("SECRET", line)
            self.assertIn("HTTP_PROXY", env_file.read_text(encoding="utf-8"))
        finally:
            env_file.unlink(missing_ok=True)
            env_file.parent.rmdir()

    def test_metacharacters_stay_in_the_private_env_file(self):
        line, env_file = backend_runtime.wmi_command_line(
            ["codex.exe", "app-server", "daemon", "start"],
            {"NO_PROXY": 'a" & calc'}, cwd=r"C:\cwd")
        try:
            self.assertNotIn("calc", line)
            self.assertIn("calc", env_file.read_text(encoding="utf-8"))
        finally:
            env_file.unlink(missing_ok=True)
            env_file.parent.rmdir()

    async def test_a_blocked_launch_retries_outside_the_job(self):
        calls = []

        async def runner(command, *, env, cwd, timeout):
            calls.append(command)
            if len(calls) == 1:
                raise BackendStartupError("后台准备失败（退出码 1）")
            return b""

        detached = Mock(return_value=4242)
        with patch.object(backend_runtime, "run", runner), \
                patch.object(backend_runtime, "create_process_outside_job", detached):
            await self.runtime().ensure_native(["codex.exe", "app-server"],
                                               {"HTTP_PROXY": "p"}, is_windows=True)
        self.assertEqual(len(calls), 1)  # the fallback does not shell out
        detached.assert_called_once()
        self.assertIn("wmi_launch.py", detached.call_args.args[0])
        self.assertNotIn("HTTP_PROXY", detached.call_args.args[0])

    async def test_the_shell_fallback_is_used_when_com_is_unavailable(self):
        calls = []

        async def runner(command, *, env, cwd, timeout):
            calls.append(command)
            if len(calls) == 1:
                raise BackendStartupError("后台准备失败（退出码 1）")
            return b""

        with patch.object(backend_runtime, "run", runner), \
                patch.object(backend_runtime, "create_process_outside_job",
                             Mock(side_effect=ImportError("no pywin32"))), \
                patch.object(backend_runtime, "log_event"), \
                patch.object(backend_runtime.shutil, "which", return_value="/bin/powershell.exe"):
            await self.runtime().ensure_native(["codex.exe", "app-server"],
                                               {"HTTP_PROXY": "p"}, is_windows=True)
        self.assertEqual(len(calls), 2)
        self.assertIn("Invoke-CimMethod", calls[1][-1])

    async def test_the_direct_reason_survives_a_failed_retry(self):
        async def runner(command, *, env, cwd, timeout):
            raise BackendStartupError("后台准备失败（退出码 1）")

        with patch.object(backend_runtime, "run", runner), \
                patch.object(backend_runtime, "create_process_outside_job",
                             Mock(side_effect=ImportError("no pywin32"))), \
                patch.object(backend_runtime.shutil, "which", return_value="/bin/powershell.exe"), \
                patch.object(backend_runtime, "log_event"):
            with self.assertRaises(BackendStartupError) as caught:
                await self.runtime().ensure_native(["codex.exe", "app-server"], {},
                                                   is_windows=True)
        self.assertIn("退出码 1", str(caught.exception))


class WindowsTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_windows_defaults_to_stdio_and_preserves_explicit_shared_choice(self):
        callbacks = dict(on_request=AsyncMock(), on_event=Mock(), on_connect=Mock(),
                         on_disconnect=Mock())
        with patch("codex_connection.IS_WINDOWS", True):
            self.assertFalse(CodexTransport({}, "/tmp", **callbacks).shared)
            self.assertTrue(CodexTransport({"sharedDaemon": True}, "/tmp", **callbacks).shared)

    async def test_release_thread_returns_the_writer_best_effort(self):
        backend = CodexBackend({}, "/tmp", lambda _: None)
        backend.transport.proc = SimpleNamespace(returncode=None)
        backend.transport.reader = SimpleNamespace(done=lambda: False)
        backend.rpc = AsyncMock(return_value={})
        self.assertTrue(await backend.release_thread("thread-1"))
        backend.rpc.assert_awaited_once_with("thread/unsubscribe", {"threadId": "thread-1"})
        self.assertEqual(backend.writer_state("thread-1"), WRITER_RELEASED)
        backend.rpc = AsyncMock(side_effect=RuntimeError("unsupported method"))
        self.assertFalse(await backend.release_thread("thread-2"))
        self.assertEqual(backend.writer_state("thread-1"), WRITER_RELEASED)
        backend.transport.proc = SimpleNamespace(returncode=1)
        backend.rpc = AsyncMock(return_value={})
        self.assertFalse(await backend.release_thread("thread-3"))

    async def test_resume_conflict_uses_the_same_follower_setup_as_following(self):
        backend = CodexBackend({}, "/tmp", lambda _: None)
        backend.transport.proc = SimpleNamespace(returncode=None)
        backend.transport.reader = SimpleNamespace(done=lambda: False)
        backend.rpc = AsyncMock(side_effect=CodexRpcError(
            -32600, "thread t already has an active writer"))
        backend.refresh_runtime = AsyncMock()
        backend.refresh_usage = AsyncMock()
        candidate = SimpleNamespace(
            thread_id="t", enabled=False, activate=Mock(), close=AsyncMock(),
            settings=AsyncMock(),
            thread_result=lambda: {"thread": {"id": "t", "cwd": "/cwd"}, "cwd": "/cwd"})
        backend.attach_app = AsyncMock(return_value=candidate)
        self.assertEqual(await backend.thread("t"), "t")
        self.assertIs(backend.app, candidate)
        self.assertEqual(backend.writer_state("t"), WRITER_FOLLOWED)
        # The shared path must configure publishing, or desktop turns go silent.
        candidate.activate.assert_called_once_with()

    async def test_shared_daemon_keeps_the_thread_loaded_while_following(self):
        backend = CodexBackend({"sharedDaemon": True}, "/tmp", lambda _: None)
        backend.transport.proc = SimpleNamespace(returncode=None)
        backend.transport.reader = SimpleNamespace(done=lambda: False)
        backend.transport.shared = True
        backend.rpc = AsyncMock(return_value={})
        # The daemon serialises writers, so unsubscribing would only lose the
        # event stream that carries another client's turns.
        self.assertFalse(await backend.release_thread("thread-1", follow=True))
        backend.rpc.assert_not_awaited()
        self.assertEqual(backend.writer_state("thread-1"), WRITER_NATIVE)

    async def test_shared_daemon_releases_a_thread_the_bridge_leaves(self):
        backend = CodexBackend({"sharedDaemon": True}, "/tmp", lambda _: None)
        backend.transport.proc = SimpleNamespace(returncode=None)
        backend.transport.reader = SimpleNamespace(done=lambda: False)
        backend.transport.shared = True
        backend.rpc = AsyncMock(return_value={})
        # /switch must unsubscribe, or every thread the bridge ever selected
        # stays locked for the App until the daemon restarts.
        self.assertTrue(await backend.release_thread("thread-1"))
        backend.rpc.assert_awaited_once_with("thread/unsubscribe", {"threadId": "thread-1"})
        self.assertEqual(backend.writer_state("thread-1"), WRITER_RELEASED)

    async def test_an_adopted_shared_thread_still_reports_a_foreign_writer(self):
        backend = CodexBackend({"sharedDaemon": True}, "/tmp", lambda _: None)
        backend.transport.proc = SimpleNamespace(returncode=None)
        backend.transport.reader = SimpleNamespace(done=lambda: False)
        backend.transport.shared = True
        backend.rpc = AsyncMock(return_value={})
        backend.set_writer(WRITER_BUSY, "thread-1")
        # Keeping the subscription must not claim a writer another client holds,
        # or the next turn would submit into a conflict it cannot recover from.
        self.assertFalse(await backend.release_thread("thread-1", follow=True))
        self.assertEqual(backend.writer_state("thread-1"), WRITER_BUSY)
        self.assertTrue(backend.writer_elsewhere("thread-1"))

    async def test_unavailable_shared_daemon_falls_back_to_a_private_server(self):
        backend = CodexBackend({"sharedDaemon": True}, "/tmp", lambda _: None)
        backend.transport.start = AsyncMock(
            side_effect=SharedDaemonUnavailable("this CLI has no complete local package"))
        with patch("backend.CodexTransport") as transport:
            transport.return_value.start = AsyncMock()
            transport.return_value.shared = False
            await backend.start()
        self.assertFalse(backend.shared)
        self.assertEqual(backend.config, {"sharedDaemon": True})
        self.assertIn("no complete local package", backend.shared_fallback)
        transport.assert_called_once()
        self.assertEqual(transport.call_args.args[0], {"sharedDaemon": False})
        transport.return_value.start.assert_awaited_once_with()

    async def test_a_busy_thread_is_retried_before_adopting(self):
        candidate = SimpleNamespace(
            shared=False,
            thread=AsyncMock(side_effect=[
                CodexRpcError(-32600, "thread t already has an active writer"), "t"]),
            adopt_thread=AsyncMock())
        with patch("profiles.asyncio.sleep", AsyncMock()):
            self.assertEqual(await resume_or_adopt(candidate, "t", True), "t")
        candidate.adopt_thread.assert_not_called()

    async def test_a_mode_change_adopts_a_thread_the_daemon_still_holds(self):
        candidate = SimpleNamespace(
            shared=False,
            thread=AsyncMock(
                side_effect=CodexRpcError(-32600, "thread t already has an active writer")),
            adopt_thread=AsyncMock(return_value="t"))
        self.assertEqual(await resume_or_adopt(candidate, "t", True, wait=0), "t")
        candidate.adopt_thread.assert_awaited_once_with("t")

    async def test_a_plain_switch_still_reports_the_writer_conflict(self):
        candidate = SimpleNamespace(
            shared=False,
            thread=AsyncMock(
                side_effect=CodexRpcError(-32600, "thread t already has an active writer")),
            adopt_thread=AsyncMock())
        with self.assertRaises(CodexRpcError):
            await resume_or_adopt(candidate, "t", False)
        candidate.adopt_thread.assert_not_called()

    async def test_writer_states_track_who_holds_the_thread(self):
        backend = CodexBackend({}, "/tmp", lambda _: None)
        backend.transport.proc = SimpleNamespace(returncode=None)
        backend.transport.reader = SimpleNamespace(done=lambda: False)
        backend.rpc = AsyncMock(return_value={"thread": {"id": "t", "cwd": "/cwd"}, "cwd": "/cwd"})
        backend.refresh_runtime = AsyncMock()
        backend.refresh_usage = AsyncMock()
        self.assertEqual(await backend.thread("t"), "t")
        self.assertEqual(backend.writer_state("t"), WRITER_NATIVE)
        self.assertFalse(backend.writer_elsewhere("t"))
        self.assertTrue(await backend.release_thread("t"))
        self.assertEqual(backend.writer_state("t"), WRITER_RELEASED)
        self.assertTrue(backend.writer_elsewhere("t"))
        self.assertFalse(backend.writer_elsewhere("other"))
        self.assertEqual(await backend.adopt_thread("t", {}), "t")
        self.assertEqual(backend.writer_state("t"), WRITER_BUSY)
        self.assertTrue(backend.writer_elsewhere("t"))

    async def test_followed_app_thread_keeps_its_subscription(self):
        # A followed thread still needs its IPC events, so it is never released.
        backend = CodexBackend({}, "/tmp", lambda _: None)
        backend.transport.proc = SimpleNamespace(returncode=None)
        backend.transport.reader = SimpleNamespace(done=lambda: False)
        backend.app = SimpleNamespace(thread_id="thread-1", connected=True)
        backend.rpc = AsyncMock(return_value={})
        self.assertFalse(await backend.release_thread("thread-1"))
        backend.rpc.assert_not_awaited()

    async def test_release_can_follow_the_desktop_that_took_the_thread(self):
        backend = CodexBackend({}, "/tmp", lambda _: None)
        backend.shared = False
        backend.transport.proc = SimpleNamespace(returncode=None)
        backend.transport.reader = SimpleNamespace(done=lambda: False)
        backend.rpc = AsyncMock(return_value={})
        backend.follow_app = AsyncMock(return_value=False)
        self.assertTrue(await backend.release_thread("thread-1"))
        backend.follow_app.assert_not_awaited()
        self.assertTrue(await backend.release_thread("thread-1", follow=True))
        backend.follow_app.assert_awaited_once_with("thread-1")

    async def test_follow_app_drops_the_released_marker_only_when_it_attaches(self):
        backend = CodexBackend({}, "/tmp", lambda _: None)
        backend.transport.proc = SimpleNamespace(returncode=None)
        backend.transport.reader = SimpleNamespace(done=lambda: False)
        backend.set_writer(WRITER_RELEASED, "thread-1")
        backend.app_available = lambda: False
        backend.attach_app = AsyncMock()
        self.assertFalse(await backend.follow_app("thread-1"))
        backend.attach_app.assert_not_awaited()
        self.assertEqual(backend.writer_state("thread-1"), WRITER_RELEASED)

        candidate = SimpleNamespace(thread_id="thread-1", enabled=False,
                                    activate=Mock(), close=AsyncMock())
        backend.app_available = lambda: True
        backend.attach_app = AsyncMock(return_value=candidate)
        self.assertTrue(await backend.follow_app("thread-1"))
        self.assertIs(backend.app, candidate)
        self.assertEqual(backend.writer_state("thread-1"), WRITER_FOLLOWED)
        self.assertEqual(backend.selected_thread, "thread-1")
        # Without activate() the follower never publishes state changes.
        candidate.activate.assert_called_once_with()

    async def test_attach_failure_never_escapes_following(self):
        backend = CodexBackend({}, "/tmp", lambda _: None)
        backend.transport.proc = SimpleNamespace(returncode=None)
        backend.transport.reader = SimpleNamespace(done=lambda: False)
        backend.app_available = lambda: True
        backend.attach_app = AsyncMock(side_effect=CodexRpcError(
            -32600, "App IPC: no-client-found"))
        self.assertFalse(await backend.follow_app("thread-1"))
        self.assertIsNone(backend.app)

    async def test_unusable_app_endpoint_does_not_replace_the_writer_conflict(self):
        backend = CodexBackend({}, "/tmp", lambda _: None)
        backend.transport.proc = SimpleNamespace(returncode=None)
        backend.transport.reader = SimpleNamespace(done=lambda: False)
        candidate = SimpleNamespace(attach=AsyncMock(side_effect=ValueError("App 返回了其它会话")),
                                    settings=AsyncMock(), close=AsyncMock())
        import app_ipc
        with tempfile.NamedTemporaryFile() as endpoint:
            with patch.object(app_ipc, "ipc_path", return_value=Path(endpoint.name)), \
                    patch.object(app_ipc, "AppSession", return_value=candidate):
                self.assertIsNone(await backend.attach_app("thread-1", {}))
        candidate.close.assert_awaited()
        self.assertIsNone(backend.app)

    async def test_stalled_turn_is_abandoned_instead_of_hanging(self):
        backend = CodexBackend({}, "/tmp", lambda _: None)
        backend.turn_idle_seconds = 0.05
        current = backend.new_turn("t", "turn-1", owned=True)
        backend.active = current
        follower = SimpleNamespace(close=AsyncMock())
        backend.app = follower
        backend.pending["tok"] = {"id": "1", "params": {}}
        with self.assertRaises(TurnStalled):
            await backend.await_turn(current)
        self.assertIsNone(backend.active)
        self.assertIsNone(backend.app)
        follower.close.assert_awaited()
        self.assertEqual(backend.pending, {})

    async def test_turn_progress_ignores_other_threads_and_account_events(self):
        backend = CodexBackend({}, "/tmp", lambda _: None)
        current = backend.new_turn("t", "turn-1", owned=True)
        backend.active = current
        initial = current["last_progress_at"]
        backend.event({"method": "account/rateLimits/updated",
                       "params": {"rateLimits": {}}})
        backend.event({"method": "item/completed",
                       "params": {"threadId": "other", "turnId": "turn-1",
                                  "item": {"id": "x", "type": "agentMessage",
                                           "phase": "commentary", "text": "x"}}})
        self.assertEqual(current["last_progress_at"], initial)
        backend.event({"method": "item/completed",
                       "params": {"threadId": "t", "turnId": "turn-1",
                                  "item": {"id": "y", "type": "agentMessage",
                                           "phase": "commentary", "text": "y"}}})
        self.assertGreaterEqual(current["last_progress_at"], initial)

    async def test_stall_wait_restarts_when_events_resume(self):
        backend = CodexBackend({}, "/tmp", lambda _: None)
        backend.turn_idle_seconds = 0.2
        current = backend.new_turn("t", "turn-1", owned=True)
        backend.active = current
        loop = asyncio.get_running_loop()
        def progress():
            current["last_progress_at"] = time.monotonic()
            backend.last_event_at = time.monotonic()
        loop.call_later(0.1, progress)
        loop.call_later(0.25, progress)
        loop.call_later(0.35, current["done"].set_result, {"ok": True})
        self.assertEqual(await backend.await_turn(current), {"ok": True})

    async def test_windows_probe_falls_back_to_the_system_proxy(self):
        backend = CodexBackend({}, "/tmp", lambda _: None)
        backend.transport.proc = SimpleNamespace(returncode=None)
        backend.transport.reader = SimpleNamespace(done=lambda: False)
        backend.transport.launch_env = {}
        backend.info = {"provider": "custom"}
        backend.rpc = AsyncMock(return_value={"config": {"model_providers": {
            "custom": {"base_url": "https://example.com/v1"}}}})
        with patch("backend.IS_WINDOWS", True), \
                patch("platform_support.getproxies",
                      return_value={"https": "http://127.0.0.1:7897", "no": "localhost;127.*"}):
            _, env = await backend.probe_target()
        self.assertEqual(env["HTTPS_PROXY"], "http://127.0.0.1:7897")
        self.assertEqual(env["NO_PROXY"], "localhost,127.*")

    async def test_windows_probe_respects_disabled_system_proxy(self):
        backend = CodexBackend({"useSystemProxy": False}, "/tmp", lambda _: None)
        backend.transport.proc = SimpleNamespace(returncode=None)
        backend.transport.reader = SimpleNamespace(done=lambda: False)
        backend.transport.launch_env = {}
        backend.info = {"provider": "custom"}
        backend.rpc = AsyncMock(return_value={"config": {"model_providers": {
            "custom": {"base_url": "https://example.com/v1"}}}})
        with patch("backend.IS_WINDOWS", True), \
                patch("platform_support.getproxies",
                      return_value={"https": "http://127.0.0.1:7897"}):
            _, env = await backend.probe_target()
        self.assertNotIn("HTTPS_PROXY", env)

    async def test_explicit_proxy_environment_wins_over_the_system_proxy(self):
        backend = CodexBackend({}, "/tmp", lambda _: None)
        backend.transport.proc = SimpleNamespace(returncode=None)
        backend.transport.reader = SimpleNamespace(done=lambda: False)
        backend.transport.launch_env = {"HTTPS_PROXY": "http://localhost:1234"}
        backend.info = {"provider": "custom"}
        backend.rpc = AsyncMock(return_value={"config": {"model_providers": {
            "custom": {"base_url": "https://example.com/v1"}}}})
        with patch("backend.IS_WINDOWS", True), \
                patch("platform_support.getproxies",
                      return_value={"https": "http://127.0.0.1:7897"}):
            _, env = await backend.probe_target()
        self.assertEqual(env["HTTPS_PROXY"], "http://localhost:1234")

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
