"""Offline network-timer contracts; subprocess and native RPC are mocked."""
import asyncio
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from latency import Latency, connection_probe, message_timestamps, provider_latency


class NetworkLatencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_tls_measurement_excludes_response_wait(self):
        process = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(
            json.dumps({"time_appconnect": .185, "time_starttransfer": 2,
                        "time_total": 3}).encode(), None)), stdin=None)
        with patch("latency.asyncio.create_subprocess_exec", AsyncMock(return_value=process)) as spawn:
            result = await connection_probe("https://api.example/v1?secret=hidden", {})
        self.assertEqual(result, "185ms")
        config = process.communicate.call_args.args[0].decode()
        self.assertNotIn("secret", config)
        self.assertNotIn("Authorization", config)
        self.assertIn("--head", spawn.call_args.args)

    async def test_completed_handshake_survives_http_timeout(self):
        process = SimpleNamespace(returncode=28, communicate=AsyncMock(return_value=(
            b'{"time_appconnect":0.2}', None)), stdin=None)
        with patch("latency.asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
            self.assertEqual(await connection_probe("https://api.example", {}), "200ms")

    async def test_connection_timeout_is_not_reported_as_zero(self):
        process = SimpleNamespace(returncode=28, communicate=AsyncMock(return_value=(
            b'{"time_appconnect":0}', None)), stdin=None)
        with patch("latency.asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
            self.assertEqual(await connection_probe("https://api.example", {}),
                             "timeout(直连,未配置代理)")

    async def test_timeout_names_the_failing_path(self):
        process = SimpleNamespace(returncode=28, communicate=AsyncMock(return_value=(
            b'{"time_appconnect":0}', None)), stdin=None)
        with patch("latency.asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
            self.assertEqual(
                await connection_probe("https://api.example", {"HTTPS_PROXY": "http://127.0.0.1:7897"}),
                "timeout(代理未响应)")
            self.assertEqual(
                await connection_probe("https://api.example", {"HTTPS_PROXY": "  "}),
                "timeout(直连,未配置代理)")

    async def test_refused_connection_is_not_called_unavailable(self):
        # curl 7 = could not connect, which a blocked direct route returns at once.
        process = SimpleNamespace(returncode=7, communicate=AsyncMock(return_value=(
            b'{"time_appconnect":0}', None)), stdin=None)
        with patch("latency.asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
            self.assertEqual(await connection_probe("https://api.example", {}),
                             "unreachable(直连,未配置代理)")
            self.assertEqual(
                await connection_probe("https://api.example", {"ALL_PROXY": "socks5://127.0.0.1:7897"}),
                "unreachable(代理未响应)")

    async def test_missing_backend_or_rpc_timeout_is_explicit(self):
        backend = SimpleNamespace(probe_target=AsyncMock(return_value=None))
        self.assertEqual(await provider_latency(backend), "unavailable(无可用目标)")
        backend.probe_target.side_effect = TimeoutError()
        self.assertEqual(await provider_latency(backend), "timeout(读取后端配置)")
        backend.probe_target.side_effect = RuntimeError("boom")
        self.assertEqual(await provider_latency(backend), "unavailable(RuntimeError)")

    async def test_cancellation_reaps_probe(self):
        entered = asyncio.Event()

        async def communicate(_):
            entered.set()
            await asyncio.Event().wait()

        process = SimpleNamespace(returncode=None, communicate=communicate,
                                  wait=AsyncMock(), stdin=None)
        process.terminate = lambda: setattr(process, "returncode", -15)
        with patch("latency.asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
            task = asyncio.create_task(connection_probe("https://api.example", {}))
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        process.wait.assert_awaited()


class MessageTimestampTests(unittest.TestCase):
    def test_falls_back_to_update_and_item_timestamps(self):
        message = {
            "update_time_ms": "1000500",
            "item_list": [{"create_time_ms": 1000600}],
        }
        self.assertEqual(message_timestamps(message), (1000500.0, 1000600))

    def test_latency_uses_first_usable_timestamp(self):
        with patch("latency.time.time", return_value=1000):
            latency = Latency()
        with patch("latency.time.time", return_value=1001), \
             patch("latency.time.perf_counter", return_value=1):
            received = latency.received_any(message_timestamps({
                "create_time_ms": 999000,
                "update_time_ms": 1000500,
                "item_list": [{"create_time_ms": 1000600}],
            }))
        self.assertEqual(received["ingress_ms"], 500)

    def test_missing_timestamps_do_not_create_a_sample(self):
        with patch("latency.time.time", return_value=1000):
            latency = Latency()
        with patch("latency.time.time", return_value=1001):
            self.assertNotIn("ingress_ms", latency.received_any(message_timestamps({
                "item_list": [{"type": 1}],
            })))
