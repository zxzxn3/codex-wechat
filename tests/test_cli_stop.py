"""CLI background-service stop wrapper, without native service operations."""
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import codex_wechat
from prompts import SERVICE_STOPPING
from service_adapter import ServiceState
from storage import Store


class StopCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_reports_missing_service_without_calling_stop(self):
        stop = AsyncMock()
        notify = AsyncMock()
        output = StringIO()
        with patch.object(codex_wechat.service_adapter, "status",
                          AsyncMock(return_value=ServiceState(False, False))), \
                patch.object(codex_wechat.service_adapter, "stop_and_wait", stop), \
                patch.object(codex_wechat, "send_stop_notification", notify), \
                redirect_stdout(output):
            await codex_wechat.stop_background_service(Path("/state"), Path("/config"))
        self.assertEqual(output.getvalue(), "未安装后台服务。\n")
        notify.assert_not_awaited()
        stop.assert_not_awaited()

    async def test_reports_stopped_service_without_calling_stop(self):
        stop = AsyncMock()
        notify = AsyncMock()
        output = StringIO()
        with patch.object(codex_wechat.service_adapter, "status",
                          AsyncMock(return_value=ServiceState(True, False))), \
                patch.object(codex_wechat.service_adapter, "stop_and_wait", stop), \
                patch.object(codex_wechat, "send_stop_notification", notify), \
                redirect_stdout(output):
            await codex_wechat.stop_background_service(Path("/state"), Path("/config"))
        self.assertEqual(output.getvalue(), "后台服务未运行。\n")
        notify.assert_not_awaited()
        stop.assert_not_awaited()

    async def test_notifies_then_stops_running_service_and_reports_success(self):
        calls = []
        notify = AsyncMock(side_effect=lambda *_: calls.append("notice"))
        stop = AsyncMock(return_value=ServiceState(True, False),
                         side_effect=lambda: calls.append("stop"))
        output = StringIO()
        with patch.object(codex_wechat.service_adapter, "status",
                          AsyncMock(return_value=ServiceState(True, True))), \
                patch.object(codex_wechat.service_adapter, "stop_and_wait", stop), \
                patch.object(codex_wechat, "send_stop_notification", notify), \
                redirect_stdout(output):
            await codex_wechat.stop_background_service(Path("/state"), Path("/config"))
        self.assertEqual(output.getvalue(), "后台服务已停止。\n")
        notify.assert_awaited_once_with(Path("/state"), Path("/config"))
        stop.assert_awaited_once_with()
        self.assertEqual(calls, ["notice", "stop"])

    async def test_stop_notification_uses_latest_context_and_hot_override(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = root / ".state"
            store = Store(state_dir, lock=False)
            store.save("account", {"user": "owner", "token": "secret"})
            store.save("bridge", {"context": "context"})
            config = root / "config.json"
            config.write_text('{"prompts":{"SERVICE_STOPPING":"bye"}}',
                              encoding="utf-8")
            client = SimpleNamespace(send=AsyncMock(return_value={}), close=AsyncMock())
            with patch.object(codex_wechat, "WeChat", return_value=client):
                await codex_wechat.send_stop_notification(state_dir, config)
        entry = client.send.await_args.args[0]
        self.assertEqual((entry["user"], entry["context"], entry["text"]),
                         ("owner", "context", "bye"))

    async def test_stop_notification_without_context_is_silent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = root / ".state"
            store = Store(state_dir, lock=False)
            store.save("account", {"user": "owner", "token": "secret"})
            client = SimpleNamespace(send=AsyncMock())
            with patch.object(codex_wechat, "WeChat", return_value=client):
                await codex_wechat.send_stop_notification(state_dir, root / "config.json")
        client.send.assert_not_awaited()

    def test_stop_notification_falls_back_from_non_object_config(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.json"
            config.write_text("null", encoding="utf-8")
            self.assertEqual(codex_wechat.stop_notification_text(config), SERVICE_STOPPING)

    async def test_stop_notification_failure_does_not_block_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = root / ".state"
            store = Store(state_dir, lock=False)
            store.save("account", {"user": "owner", "token": "secret"})
            store.save("bridge", {"context": "context"})
            client = SimpleNamespace(
                send=AsyncMock(side_effect=RuntimeError("send failed")),
                close=AsyncMock())
            stop = AsyncMock(return_value=ServiceState(True, False))
            error_output = StringIO()
            with patch.object(codex_wechat.service_adapter, "status",
                              AsyncMock(return_value=ServiceState(True, True))), \
                    patch.object(codex_wechat.service_adapter, "stop_and_wait", stop), \
                    patch.object(codex_wechat, "WeChat", return_value=client), \
                    redirect_stderr(error_output):
                await codex_wechat.stop_background_service(
                    state_dir, root / "config.json")
        stop.assert_awaited_once_with()
        self.assertIn("关闭通知未发送：RuntimeError", error_output.getvalue())

    async def test_main_routes_stop_before_taking_the_bridge_lock(self):
        stop = AsyncMock()
        startup_store = AsyncMock()
        with patch.object(sys, "argv", ["codex_wechat.py", "stop"]), \
                patch.object(codex_wechat.os, "umask"), \
                patch.object(codex_wechat, "stop_background_service", stop), \
                patch.object(codex_wechat, "startup_store", startup_store):
            await codex_wechat.main()
        stop.assert_awaited_once_with(codex_wechat.data_root() / ".state",
                                      codex_wechat.data_root() / "config.json")
        startup_store.assert_not_awaited()

    def test_windows_entry_routes_stop(self):
        script = (Path(__file__).parents[1] / "codex-wechat.ps1").read_text(encoding="utf-8")
        self.assertIn("'stop'", script)
        branch = script.split("'stop' {", 1)[1].split("'service-stop' {", 1)[0]
        self.assertIn(
            "& $VenvPython -u $Agent stop --state-dir $StateDir --config $ConfigPath",
            branch,
        )
