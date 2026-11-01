"""Lazy service registration/handoff, using only synthetic native operations."""
import asyncio
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

import autostart
from codex_wechat import Bridge, startup_store
from errors import UserFacingError
from storage import Store


class LazyRegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_service_is_off_and_not_installed_by_query(self):
        native = AsyncMock(return_value="LoadState=not-found\nFragmentPath=\nUnitFileState=")
        with patch.object(autostart, "IS_WINDOWS", False), patch.object(autostart, "systemctl", native):
            self.assertFalse(await autostart.status())
            self.assertFalse(await autostart.installed())
        self.assertTrue(all(call.args[0] == "show" for call in native.await_args_list))

    async def test_first_enable_registers_then_starts_only_when_requested(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            project = home / ".local/share/codex-wechat"
            project.mkdir(parents=True)
            (project / autostart.UNIT).write_text("[Unit]\n")
            for should_start in (True, False):
                native = AsyncMock()
                with patch.object(autostart, "IS_WINDOWS", False), \
                        patch.object(autostart, "PROJECT_ROOT", project), \
                        patch("autostart.Path.home", return_value=home), \
                        patch.object(autostart, "_linux_status", AsyncMock(side_effect=[None, True])), \
                        patch.object(autostart, "systemctl", native):
                    self.assertTrue(await autostart.set_enabled(True, start_if_missing=should_start))
                args = [call.args for call in native.await_args_list]
                self.assertEqual(args[0], ("enable", str(project / autostart.UNIT)))
                self.assertEqual(("--no-block", "start", autostart.UNIT) in args, should_start)

    async def test_disable_missing_service_does_not_create_anything(self):
        native = AsyncMock()
        with patch.object(autostart, "IS_WINDOWS", False), \
                patch.object(autostart, "_linux_status", AsyncMock(return_value=None)), \
                patch.object(autostart, "systemctl", native):
            self.assertFalse(await autostart.set_enabled(False))
        native.assert_not_called()

    async def test_existing_service_toggle_does_not_start_it(self):
        native = AsyncMock()
        with patch.object(autostart, "IS_WINDOWS", False), \
                patch.object(autostart, "_linux_status", AsyncMock(side_effect=[False, True])), \
                patch.object(autostart, "systemctl", native):
            await autostart.set_enabled(True)
        self.assertTrue(all("start" not in call.args for call in native.await_args_list))


class HandoffTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / ".state")
        self.bridge = Bridge(self.store, {"user": "owner"}, {})
        self.bridge.state["context"] = "context"

    async def asyncTearDown(self):
        self.store.lock.close()
        self.temp.cleanup()

    async def test_reply_is_saved_before_service_start_and_input_is_preserved(self):
        bridge = self.bridge
        bridge.state["inbox"] = [{"id": "queued", "text": "later"}]
        events = []
        original_reply = bridge.reply

        def reply(text):
            original_reply(text)
            events.append("reply")
            bridge.outbox_drained.set()

        async def start(*args):
            events.append("start")
            self.assertTrue(bridge.restarting)
            self.assertEqual(self.store.load("bridge")["outbox"][-1]["text"], "自启: 开启")

        def shutdown():
            events.append("shutdown")
            raise asyncio.CancelledError

        bridge.reply, bridge.shutdown_requested = reply, shutdown
        with patch("codex_wechat.data_root", return_value=self.root), \
                patch("autostart.set_enabled", AsyncMock()) as enable, \
                patch("codex_wechat.service_control.systemctl", start):
            with self.assertRaises(asyncio.CancelledError):
                await bridge.enable_background_service()
        enable.assert_awaited_once_with(True, start_if_missing=False)
        self.assertEqual(events, ["reply", "start", "shutdown"])
        self.assertEqual(bridge.state["inbox"][0]["id"], "queued")

    async def test_busy_or_custom_paths_refuse_handoff_without_registration(self):
        enable = AsyncMock()
        with patch("codex_wechat.data_root", return_value=self.root), \
                patch("autostart.set_enabled", enable):
            self.bridge.running = True
            with self.assertRaises(UserFacingError):
                await self.bridge.enable_background_service()
        enable.assert_not_called()
        self.bridge.running = False
        with patch("codex_wechat.data_root", return_value=self.root / "other"), \
                patch("autostart.set_enabled", enable):
            with self.assertRaises(UserFacingError):
                await self.bridge.enable_background_service()
        enable.assert_not_called()

    async def test_managed_start_waits_for_foreground_lock(self):
        expected = object()
        args = SimpleNamespace(state_dir=self.root / ".state", action="start")
        with patch("codex_wechat.IS_WINDOWS", False), \
                patch("codex_wechat.Store", side_effect=[RuntimeError("locked"), expected]) as store, \
                patch("codex_wechat.service_control.require_service_owner", AsyncMock()) as owner, \
                patch("codex_wechat.asyncio.sleep", AsyncMock()):
            self.assertIs(await startup_store(args), expected)
        self.assertEqual(store.call_count, 2)
        owner.assert_awaited_once()

    async def test_unmanaged_second_process_does_not_wait_or_take_over(self):
        args = SimpleNamespace(state_dir=self.root / ".state", action="start")
        with patch("codex_wechat.IS_WINDOWS", False), \
                patch("codex_wechat.Store", side_effect=RuntimeError("locked")) as store, \
                patch("codex_wechat.service_control.require_service_owner", AsyncMock(side_effect=ValueError("not managed"))):
            with self.assertRaises(RuntimeError):
                await startup_store(args)
        store.assert_called_once()
