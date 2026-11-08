"""Contracts for backend connection and recovery coordination."""
import asyncio
import unittest
from unittest.mock import AsyncMock, Mock

from codex_wechat.backend_session import BackendSession


class BackendSessionTests(unittest.IsolatedAsyncioTestCase):
    def bridge(self, backend):
        return type("FakeBridge", (), {
            "connection_lock": asyncio.Lock(),
            "backend": backend,
            "backend_started": False,
            "loaded": True,
            "backend_connection": Mock(),
            "input_paused": True,
            "wake": asyncio.Event(),
        })()

    async def test_ensure_connects_once_and_publishes_the_connection(self):
        backend = type("FakeBackend", (), {
            "connected": False,
            "close": AsyncMock(),
            "start": AsyncMock(),
        })()
        bridge = self.bridge(backend)
        await BackendSession(bridge).ensure()
        backend.start.assert_awaited_once()
        bridge.backend_connection.assert_called_once_with(backend, True)
        self.assertTrue(bridge.backend_started)
        self.assertFalse(bridge.input_paused)
        self.assertTrue(bridge.wake.is_set())

    async def test_start_failure_is_closed_and_not_marked_started(self):
        backend = type("FakeBackend", (), {
            "connected": False,
            "close": AsyncMock(),
            "start": AsyncMock(side_effect=OSError("start failed")),
        })()
        bridge = self.bridge(backend)
        with self.assertRaisesRegex(OSError, "start failed"):
            await BackendSession(bridge).ensure()
        self.assertFalse(bridge.backend_started)
        self.assertEqual(backend.close.await_count, 2)
        bridge.backend_connection.assert_not_called()

    async def test_external_active_turn_is_reconciled(self):
        backend = type("FakeBackend", (), {
            "active": {"owned": False},
            "app": None,
            "refresh_runtime": AsyncMock(),
        })()
        bridge = self.bridge(backend)
        await BackendSession(bridge).reconcile_external_active()
        backend.refresh_runtime.assert_awaited_once()

    async def test_owned_turn_is_not_polled(self):
        backend = type("FakeBackend", (), {
            "active": {"owned": True},
            "app": None,
            "refresh_runtime": AsyncMock(),
        })()
        bridge = self.bridge(backend)
        await BackendSession(bridge).reconcile_external_active()
        backend.refresh_runtime.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
