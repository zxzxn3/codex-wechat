"""Lazy service registration and startup ownership, using synthetic operations."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import autostart
from codex_wechat import startup_store


class LazyRegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_service_is_off_and_not_installed_by_query(self):
        native = AsyncMock(return_value="LoadState=not-found\nFragmentPath=\nUnitFileState=")
        with patch.object(autostart, "IS_WINDOWS", False), patch.object(autostart, "systemctl", native):
            self.assertFalse(await autostart.status())
            self.assertFalse(await autostart.installed())
        self.assertTrue(all(call.args[0] == "show" for call in native.await_args_list))

    async def test_autostart_does_not_register_a_missing_service(self):
        native = AsyncMock()
        with patch.object(autostart, "IS_WINDOWS", False), \
                patch.object(autostart, "_linux_status", AsyncMock(return_value=None)), \
                patch.object(autostart, "systemctl", native):
            with self.assertRaisesRegex(ValueError, "重新运行安装器"):
                await autostart.set_enabled(True)
        native.assert_not_called()

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
        self.assertEqual(native.await_args_list[0].args, ("enable", autostart.UNIT))
        self.assertTrue(all("start" not in call.args for call in native.await_args_list))


class StartupOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    async def asyncTearDown(self):
        self.temp.cleanup()

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
