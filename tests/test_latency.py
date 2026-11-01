"""Offline network-timer contracts; subprocess and native RPC are mocked."""
import asyncio
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from latency import connection_probe, provider_latency


class NetworkLatencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_tls_measurement_excludes_response_wait(self):
        process = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(
            json.dumps({"time_appconnect": .185, "time_starttransfer": 2,
                        "time_total": 3}).encode(), None)))
        with patch("latency.asyncio.create_subprocess_exec", AsyncMock(return_value=process)) as spawn:
            result = await connection_probe("https://api.example/v1?secret=hidden", {})
        self.assertEqual(result, "185ms")
        config = process.communicate.call_args.args[0].decode()
        self.assertNotIn("secret", config)
        self.assertNotIn("Authorization", config)
        self.assertIn("--head", spawn.call_args.args)

    async def test_completed_handshake_survives_http_timeout(self):
        process = SimpleNamespace(returncode=28, communicate=AsyncMock(return_value=(
            b'{"time_appconnect":0.2}', None)))
        with patch("latency.asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
            self.assertEqual(await connection_probe("https://api.example", {}), "200ms")

    async def test_connection_timeout_is_not_reported_as_zero(self):
        process = SimpleNamespace(returncode=28, communicate=AsyncMock(return_value=(
            b'{"time_appconnect":0}', None)))
        with patch("latency.asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
            self.assertEqual(await connection_probe("https://api.example", {}), "timeout")

    async def test_missing_backend_or_rpc_timeout_is_explicit(self):
        backend = SimpleNamespace(probe_target=AsyncMock(return_value=None))
        self.assertEqual(await provider_latency(backend), "unavailable")
        backend.probe_target.side_effect = asyncio.TimeoutError()
        self.assertEqual(await provider_latency(backend), "timeout")

    async def test_cancellation_reaps_probe(self):
        entered = asyncio.Event()

        async def communicate(_):
            entered.set()
            await asyncio.Event().wait()

        process = SimpleNamespace(returncode=None, communicate=communicate, wait=AsyncMock())
        process.terminate = lambda: setattr(process, "returncode", -15)
        with patch("latency.asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
            task = asyncio.create_task(connection_probe("https://api.example", {}))
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        process.wait.assert_awaited()
