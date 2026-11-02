"""Offline contracts for native UI helpers; no desktop notification is sent."""
import asyncio
import base64
import contextlib
import io
import subprocess
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

import platform_ui


class FakeProcess:
    def __init__(self, returncode=0, block=False):
        self.returncode = None if block else returncode
        self.killed = False
        self.waited = False
        self.started = asyncio.Event()
        self.finished = asyncio.Event()
        if not block:
            self.finished.set()

    async def wait(self):
        self.started.set()
        await self.finished.wait()
        self.waited = True
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9
        self.finished.set()


class PlatformUiTests(unittest.TestCase):
    def test_qrencode_preferred_and_url_goes_through_stdin(self):
        completed = subprocess.CompletedProcess([], 0)
        with patch.object(platform_ui.shutil, "which",
                          return_value="/usr/bin/qrencode"), \
             patch.object(platform_ui.subprocess, "run",
                          return_value=completed) as run, \
             patch.object(platform_ui, "subprocess_options",
                          return_value={"fixture": True}):
            self.assertTrue(platform_ui.show_qr("https://example.invalid/qr"))
        self.assertEqual(
            run.call_args.args[0],
            ["/usr/bin/qrencode", "-t", "ANSIUTF8", "-o", "-"],
        )
        self.assertEqual(
            run.call_args.kwargs["input"],
            b"https://example.invalid/qr",
        )
        self.assertEqual(run.call_args.kwargs["timeout"], 5)
        self.assertEqual(run.call_args.kwargs["fixture"], True)

    def test_qrencode_failure_falls_back_to_optional_python_qrcode(self):
        printed = []

        class FakeQRCode:
            def __init__(self, border):
                self.border = border

            def add_data(self, url):
                self.url = url

            def print_ascii(self, *, invert):
                printed.append((self.url, self.border, invert))

        module = types.SimpleNamespace(QRCode=FakeQRCode)
        with patch.object(platform_ui.shutil, "which",
                          return_value="/usr/bin/qrencode"), \
             patch.object(platform_ui.subprocess, "run",
                          side_effect=subprocess.CalledProcessError(1, "qrencode")), \
             patch.object(platform_ui.importlib, "import_module",
                          return_value=module):
            self.assertTrue(platform_ui.show_qr("https://example.invalid/qr"))
        self.assertEqual(
            printed,
            [("https://example.invalid/qr", 4, True)],
        )

    def test_no_qr_encoder_returns_false_without_fake_native_api(self):
        for windows in (False, True):
            with patch.object(platform_ui, "IS_WINDOWS", windows), \
                 patch.object(platform_ui.shutil, "which", return_value=None), \
                 patch.object(platform_ui.importlib, "import_module",
                              side_effect=ImportError):
                self.assertFalse(platform_ui.show_qr("https://example.invalid/qr"))

    def test_python_qr_failure_returns_false_without_raising(self):
        with patch.object(platform_ui.shutil, "which", return_value=None), \
             patch.object(platform_ui.importlib, "import_module",
                          return_value=types.SimpleNamespace(
                              QRCode=lambda **_: (_ for _ in ()).throw(ValueError())
                          )):
            self.assertFalse(platform_ui.show_qr("https://example.invalid/qr"))

    def test_noninteractive_notification_has_no_console_output(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(platform_ui, "IS_WINDOWS", False), \
             patch.object(platform_ui.shutil, "which", return_value=None), \
             contextlib.redirect_stdout(stdout), \
             contextlib.redirect_stderr(stderr):
            result = asyncio.run(platform_ui.notify("title", "body"))
        self.assertFalse(result)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")


class AsyncNotificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_linux_notification_uses_argv_separator_and_timeout(self):
        runner = AsyncMock(return_value=True)
        with patch.object(platform_ui, "IS_WINDOWS", False), \
             patch.object(platform_ui.shutil, "which",
                          return_value="/usr/bin/notify-send"), \
             patch.object(platform_ui, "_run_quiet", runner):
            self.assertTrue(await platform_ui.notify("-title", "body"))
        command = runner.await_args.args[0]
        self.assertEqual(command[0], "/usr/bin/notify-send")
        self.assertIn("--", command)
        self.assertEqual(command[-2:], ["-title", "body"])
        self.assertEqual(runner.await_args.kwargs["timeout"], 3)

    async def test_windows_notification_uses_native_balloon_and_encoded_data(self):
        runner = AsyncMock(return_value=True)

        def which(name):
            return "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe" \
                if name == "powershell.exe" else None

        with patch.object(platform_ui, "IS_WINDOWS", True), \
             patch.object(platform_ui.shutil, "which", side_effect=which), \
             patch.object(platform_ui, "_run_quiet", runner):
            self.assertTrue(await platform_ui.notify("绑定失效", "运行 relink"))

        command = runner.await_args.args[0]
        encoded_index = command.index("-EncodedCommand") + 1
        script = base64.b64decode(command[encoded_index]).decode("utf-16le")
        self.assertIn("System.Windows.Forms.NotifyIcon", script)
        self.assertIn("ShowBalloonTip", script)
        self.assertNotIn("绑定失效", script)
        self.assertNotIn("运行 relink", command)
        env = runner.await_args.kwargs["env"]
        self.assertEqual(
            base64.b64decode(env[platform_ui.NOTIFY_TITLE_ENV]).decode(),
            "绑定失效",
        )
        self.assertEqual(
            base64.b64decode(env[platform_ui.NOTIFY_BODY_ENV]).decode(),
            "运行 relink",
        )
        self.assertEqual(runner.await_args.kwargs["timeout"], 7)

    async def test_windows_without_powershell_returns_false(self):
        with patch.object(platform_ui, "IS_WINDOWS", True), \
             patch.object(platform_ui.shutil, "which", return_value=None), \
             patch.object(platform_ui, "_run_quiet", AsyncMock()) as runner:
            self.assertFalse(await platform_ui.notify("title", "body"))
        runner.assert_not_awaited()

    async def test_nonzero_process_returns_false(self):
        process = FakeProcess(returncode=1)
        with patch.object(platform_ui.asyncio, "create_subprocess_exec",
                          AsyncMock(return_value=process)):
            self.assertFalse(await platform_ui._run_quiet(["fixture"], timeout=1))
        self.assertTrue(process.waited)

    async def test_timeout_kills_and_reaps(self):
        process = FakeProcess(block=True)

        async def timeout(coroutine, _timeout):
            coroutine.close()
            raise asyncio.TimeoutError

        with patch.object(platform_ui.asyncio, "create_subprocess_exec",
                          AsyncMock(return_value=process)), \
             patch.object(platform_ui.asyncio, "wait_for",
                          side_effect=timeout):
            self.assertFalse(await platform_ui._run_quiet(["fixture"], timeout=0.01))
        self.assertTrue(process.killed)
        self.assertTrue(process.waited)

    async def test_cancellation_kills_reaps_and_propagates(self):
        process = FakeProcess(block=True)
        with patch.object(platform_ui.asyncio, "create_subprocess_exec",
                          AsyncMock(return_value=process)):
            task = asyncio.create_task(
                platform_ui._run_quiet(["fixture"], timeout=30)
            )
            await process.started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(process.killed)
        self.assertTrue(process.waited)

    async def test_spawn_failure_returns_false(self):
        with patch.object(platform_ui.asyncio, "create_subprocess_exec",
                          AsyncMock(side_effect=OSError("missing"))):
            self.assertFalse(await platform_ui._run_quiet(["fixture"], timeout=1))


if __name__ == "__main__":
    unittest.main()
