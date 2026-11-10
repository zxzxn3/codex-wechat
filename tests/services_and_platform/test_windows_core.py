"""Portable core contracts; simulated Windows branches never run system tools."""
import asyncio
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from codex_wechat.backend import (CodexBackend, TurnStalled, WRITER_BUSY, WRITER_FOLLOWED,
                     WRITER_NATIVE, WRITER_RELEASED)
from codex_wechat import backend_runtime
from codex_wechat.codex_transport import CodexTransport, CodexRpcError, SharedDaemonUnavailable
from codex_wechat.errors import BackendStartupError
from codex_wechat.pipe_websocket import PipeWebSocket
from codex_wechat.profiles import definitions, resume_or_adopt
from codex_wechat.storage import Store
from codex_wechat import platform_support
from websockets.server import ServerProtocol
from websockets.http11 import Request
from websockets.frames import Frame, Opcode


def xdg_user_data_dir(name, appauthor=False):
    """Stand in for platformdirs' Linux branch so Linux cases run on any host."""
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return str(Path(base) / name)


class PlatformTests(unittest.TestCase):
    def test_existing_directory_is_tightened(self):
        from codex_wechat import platform_support
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
                patch("codex_wechat.platform_support.shutil.which", return_value=explicit) as which:
            self.assertEqual(platform_support.resolve_command([explicit, "app-server"], {}),
                             [explicit, "app-server"])
        which.assert_called_once_with(explicit, path=None)

    def test_native_tool_output_is_decoded_without_raising(self):
        # icacls prints in the console code page; PYTHONUTF8=1 makes a strict
        # UTF-8 decode fail on a Chinese Windows host.
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(platform_support, "IS_WINDOWS", True), \
                    patch("codex_wechat.platform_support.account_identity", return_value="*S-1-5-21"), \
                    patch.object(platform_support.subprocess, "CREATE_NO_WINDOW",
                                 0x08000000, create=True), \
                    patch("codex_wechat.platform_support.subprocess.run") as run:
                run.return_value.returncode = 0
                platform_support.make_private(Path(directory))
        options = run.call_args.kwargs
        self.assertEqual(options["encoding"], "utf-8")
        self.assertEqual(options["errors"], "replace")

    def test_identity_probe_falls_back_when_the_tool_output_is_undecodable(self):
        undecodable = UnicodeDecodeError("utf-8", b"\xd2\xd1", 0, 1, "invalid start byte")
        with patch.object(platform_support, "_ACCOUNT_IDENTITY", None), \
                patch.object(platform_support.subprocess, "run", side_effect=undecodable), \
                patch.dict("os.environ", {"USERDOMAIN": "HOST", "USERNAME": "tester"}):
            self.assertEqual(platform_support.account_identity(), "HOST\\tester")

    def test_windows_paths_and_optional_launcher(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(platform_support, "IS_WINDOWS", True), \
             patch.object(platform_support, "INSTALL_ROOT", Path(directory)), \
             patch.object(platform_support, "_platform_user_data_dir",
                          lambda name, appauthor=False: Path("/tmp/local") / name), \
             patch.dict("os.environ", {"LOCALAPPDATA": "/tmp/local",
                                       "CODEX_WECHAT_DATA_ROOT": ""}):
            # An ambient override (a test-isolated run sets one) must not leak in.
            self.assertEqual(platform_support.data_root(),
                             (Path("/tmp/local/codex-wechat")).resolve())
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(platform_support, "IS_WINDOWS", False), \
                patch.object(platform_support, "INSTALL_ROOT", Path(directory)), \
                patch.dict("os.environ", {"CODEX_WECHAT_DATA_ROOT": "/tmp/stable-data"}):
            self.assertEqual(platform_support.data_root(), Path("/tmp/stable-data").resolve())
        with patch("codex_wechat.profiles.IS_WINDOWS", True):
            self.assertEqual(set(definitions({})), {"codex"})
            self.assertEqual(set(definitions({"backends": {"deepseek": {
                "command": ["custom.exe", "app-server"]}}})), {"deepseek"})

    def test_linux_default_data_root_uses_xdg_and_has_a_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            install = Path(directory) / "install"
            install.mkdir()
            data_home = Path(directory) / "xdg"
            with patch.object(platform_support, "IS_WINDOWS", False), \
                    patch.object(platform_support, "INSTALL_ROOT", install), \
                    patch.object(platform_support, "_platform_user_data_dir", xdg_user_data_dir), \
                    patch.dict("os.environ", {
                        "CODEX_WECHAT_DATA_ROOT": "",
                        "XDG_DATA_HOME": str(data_home),
                    }, clear=False):
                self.assertEqual(platform_support.data_root(),
                                 (data_home / "codex-wechat").resolve())
            with patch.object(platform_support, "IS_WINDOWS", False), \
                    patch.object(platform_support, "INSTALL_ROOT", install), \
                    patch.object(platform_support, "_platform_user_data_dir", None), \
                    patch.dict("os.environ", {
                        "CODEX_WECHAT_DATA_ROOT": "",
                        "XDG_DATA_HOME": str(data_home),
                    }, clear=False):
                self.assertEqual(platform_support.data_root(),
                                 (data_home / "codex-wechat").resolve())

    def test_linux_runtime_paths_split_config_state_and_data(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            install = base / "install"
            install.mkdir()
            with patch.object(platform_support, "IS_WINDOWS", False), \
                    patch.object(platform_support, "INSTALL_ROOT", install), \
                    patch.dict(os.environ, {
                        "CODEX_WECHAT_DATA_ROOT": "",
                        "XDG_DATA_HOME": str(base / "share"),
                        "XDG_CONFIG_HOME": str(base / "config"),
                        "XDG_STATE_HOME": str(base / "state"),
                    }, clear=False):
                paths = platform_support.runtime_paths()
            self.assertEqual(paths.data_root, (base / "share/codex-wechat").resolve())
            self.assertEqual(paths.config_file,
                             (base / "config/codex-wechat/config.json").resolve())
            self.assertEqual(paths.state_dir,
                             (base / "state/codex-wechat").resolve())
            self.assertEqual(paths.workspace_dir,
                             (base / "share/codex-wechat/workspace").resolve())
            self.assertEqual(paths.logs_dir,
                             (base / "state/codex-wechat/logs").resolve())
            self.assertEqual(paths.shared_dir,
                             (base / "state/codex-wechat/shared").resolve())

    def test_linux_persisted_xdg_roots_win_over_service_manager_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            install = base / "install"
            install.mkdir()
            config = base / "custom-config/codex-wechat"
            state = base / "custom-state/codex-wechat"
            (install / platform_support.CONFIG_ROOT_FILE).write_text(
                str(config) + "\n", encoding="utf-8")
            (install / platform_support.STATE_ROOT_FILE).write_text(
                str(state) + "\n", encoding="utf-8")
            with patch.object(platform_support, "IS_WINDOWS", False), \
                    patch.object(platform_support, "INSTALL_ROOT", install), \
                    patch.dict(os.environ, {
                        "XDG_CONFIG_HOME": str(base / "default-config"),
                        "XDG_STATE_HOME": str(base / "default-state"),
                    }, clear=False):
                paths = platform_support.runtime_paths()
            self.assertEqual(paths.config_file, config / "config.json")
            self.assertEqual(paths.state_dir, state)

    def test_windows_runtime_paths_keep_the_existing_unified_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            install = Path(directory) / "install"
            install.mkdir()
            with patch.object(platform_support, "IS_WINDOWS", True), \
                    patch.object(platform_support, "INSTALL_ROOT", install), \
                    patch.object(platform_support, "_platform_user_data_dir",
                                 lambda name, appauthor=False: Path(directory) / "local" / name), \
                    patch.dict(os.environ, {"CODEX_WECHAT_DATA_ROOT": ""}, clear=False):
                paths = platform_support.runtime_paths()
            data = (Path(directory) / "local/codex-wechat").resolve()
            self.assertEqual(paths.data_root, data)
            self.assertEqual(paths.config_file, data / "config.json")
            self.assertEqual(paths.state_dir, data / ".state")
            self.assertEqual(paths.workspace_dir, data / "workspace")
            self.assertEqual(paths.logs_dir, data / "logs")
            self.assertEqual(paths.shared_dir, data / "shared")

    def test_saved_data_root_marker_is_used_without_an_environment_override(self):
        with tempfile.TemporaryDirectory() as directory:
            install = Path(directory) / "install"
            install.mkdir()
            data = Path(directory) / "custom data"
            data.mkdir()
            (install / platform_support.DATA_ROOT_FILE).write_text(
                str(data) + "\n", encoding="utf-8")
            with patch.object(platform_support, "INSTALL_ROOT", install), \
                    patch.dict("os.environ", {"CODEX_WECHAT_DATA_ROOT": ""},
                               clear=False):
                self.assertEqual(platform_support.data_root(), data.resolve())

    def test_environment_and_saved_data_root_must_agree(self):
        with tempfile.TemporaryDirectory() as directory:
            install = Path(directory) / "install"
            install.mkdir()
            saved = Path(directory) / "saved"
            other = Path(directory) / "other"
            saved.mkdir()
            other.mkdir()
            (install / platform_support.DATA_ROOT_FILE).write_text(
                str(saved) + "\n", encoding="utf-8")
            with patch.object(platform_support, "INSTALL_ROOT", install), \
                    patch.dict("os.environ", {"CODEX_WECHAT_DATA_ROOT": str(saved)},
                               clear=False):
                self.assertEqual(platform_support.data_root(), saved.resolve())
            with patch.object(platform_support, "INSTALL_ROOT", install), \
                    patch.dict("os.environ", {"CODEX_WECHAT_DATA_ROOT": str(other)},
                               clear=False):
                with self.assertRaisesRegex(ValueError, "不一致"):
                    platform_support.data_root()

    def test_windows_refuses_implicit_shell(self):
        with patch.object(platform_support, "IS_WINDOWS", True), \
                patch("codex_wechat.platform_support.shutil.which", return_value="/tmp/codex.cmd"):
            with self.assertRaises(ValueError):
                platform_support.resolve_command(["codex", "app-server"], {})
        with patch.object(platform_support, "IS_WINDOWS", True), \
                patch("codex_wechat.platform_support.shutil.which", return_value="/tmp/codex.exe"):
            self.assertEqual(platform_support.resolve_command(["codex", "app-server"], {}),
                             ["/tmp/codex.exe", "app-server"])

    def test_windows_store_uses_byte_lock_and_unicode(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch("codex_wechat.storage.IS_WINDOWS", True), \
                patch("codex_wechat.storage.msvcrt", SimpleNamespace(locking=Mock(), LK_NBLCK=2), create=True):
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
        real_mkdtemp = tempfile.mkdtemp
        with tempfile.TemporaryDirectory() as scratch, \
                patch.object(backend_runtime.tempfile, "mkdtemp",
                             lambda **kwargs: real_mkdtemp(dir=scratch, **kwargs)), \
                patch.object(backend_runtime, "run", runner), \
                patch.object(backend_runtime, "create_process_outside_job", detached):
            await self.runtime().ensure_native(["codex.exe", "app-server"],
                                               {"HTTP_PROXY": "p"}, is_windows=True)
        self.assertEqual(len(calls), 1)  # the fallback does not shell out
        detached.assert_called_once()
        self.assertIn("wmi_launch.py", detached.call_args.args[0])
        self.assertNotIn("HTTP_PROXY", detached.call_args.args[0])

    async def test_a_successful_launch_sweeps_the_env_file(self):
        async def runner(command, *, env, cwd, timeout):
            raise BackendStartupError("后台准备失败（退出码 1）")

        real_mkdtemp = tempfile.mkdtemp
        with tempfile.TemporaryDirectory() as scratch, \
                patch.object(backend_runtime.tempfile, "mkdtemp",
                             lambda **kwargs: real_mkdtemp(dir=scratch, **kwargs)), \
                patch.object(backend_runtime, "ENV_FILE_SWEEP_SECONDS", 0.02), \
                patch.object(backend_runtime, "run", runner), \
                patch.object(backend_runtime, "create_process_outside_job", Mock(return_value=4242)):
            await self.runtime().ensure_native(["codex.exe", "app-server"],
                                               {"HTTP_PROXY": "p"}, is_windows=True)
            await asyncio.sleep(0.1)
            leftovers = list(Path(scratch).rglob("env.json"))
        self.assertEqual(leftovers, [])

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
        with patch("codex_wechat.codex_connection.IS_WINDOWS", True):
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
        with patch("codex_wechat.backend.CodexTransport") as transport:
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
        with patch("codex_wechat.profiles.asyncio.sleep", AsyncMock()):
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
        from codex_wechat import app_ipc
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
        with patch("codex_wechat.backend.IS_WINDOWS", True), \
                patch("codex_wechat.platform_support.getproxies",
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
        with patch("codex_wechat.backend.IS_WINDOWS", True), \
                patch("codex_wechat.platform_support.getproxies",
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
        with patch("codex_wechat.backend.IS_WINDOWS", True), \
                patch("codex_wechat.platform_support.getproxies",
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
        with patch("codex_wechat.backend.IS_WINDOWS", True), patch.dict("os.environ", {"API_KEY": "private"}):
            endpoint, env = await backend.probe_target()
        self.assertEqual(endpoint, "https://example.com/v1")
        self.assertEqual(env["HTTPS_PROXY"], "http://localhost:1234")
        self.assertNotIn("API_KEY", env)


class AppAvailabilityTests(unittest.TestCase):
    """pathlib raises for a busy pipe instead of reporting it missing."""

    def _backend(self):
        return CodexBackend({}, "/tmp", lambda _: None)

    def test_busy_pipe_still_counts_as_available(self):
        busy = OSError("所有的管道实例都在使用中。")
        busy.winerror = 231
        with patch("codex_wechat.app_ipc.ipc_path", return_value=r"\\.\pipe\codex-ipc"), \
                patch("codex_wechat.backend.Path.exists", side_effect=busy):
            self.assertTrue(self._backend().app_available())

    def test_other_pipe_errors_mean_unavailable(self):
        with patch("codex_wechat.app_ipc.ipc_path", return_value=r"\\.\pipe\codex-ipc"), \
                patch("codex_wechat.backend.Path.exists", side_effect=OSError(2, "missing")):
            self.assertFalse(self._backend().app_available())

    def test_without_a_candidate_path_nothing_is_available(self):
        with patch("codex_wechat.app_ipc.ipc_path", return_value=None):
            self.assertFalse(self._backend().app_available())
