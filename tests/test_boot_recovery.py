"""Shutdown recovery contracts using only temporary state and mocked services."""
import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from agent_wechat import Bridge
from codex_transport import CodexTransport
import daemon_control
from errors import BackendStartupError, UserFacingError, backend_reason
from latency import Latency
from records import InputPhase
from storage import Store


class BootRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "state")
        self.bridge = Bridge(self.store, {"user": "owner"}, {})
        self.bridge.state["context"] = "context"

    def tearDown(self):
        self.store.lock.close()
        self.temp.cleanup()

    def job(self, phase=InputPhase.PREPARING):
        return {"id": "one", "clientId": "stable", "text": "work", "phase": phase, "ingress_ms": 23926394}

    def test_old_messages_and_saved_jobs_are_not_network_samples(self):
        with patch("latency.time.time", return_value=1000):
            latency = Latency()
        with patch("latency.time.time", return_value=1001):
            self.assertNotIn("ingress_ms", latency.received(1000000 - 3600000))
            self.assertEqual(latency.received(1000500)["ingress_ms"], 500)
        self.bridge.state["inbox"] = [self.job()]
        self.bridge.recover_inputs()
        self.assertNotIn("ingress_ms", self.bridge.state["inbox"][0])
        self.assertEqual(self.bridge.state["inbox"][0]["clientId"], "stable")

    async def test_pending_work_and_recovery_lock_do_not_prevent_restart(self):
        b = self.bridge
        job = self.job()
        b.state["inbox"] = [job]
        b.backend_recovering = True
        b.reply = lambda *_: None  # No sender needed for this scheduling test.
        with patch("agent_wechat.service_control.require_service_owner", AsyncMock()), \
             patch("agent_wechat.service_control.restart", AsyncMock(side_effect=RuntimeError("simulated"))) as restart:
            async with b.session_lock:
                with self.assertRaises(RuntimeError):
                    await b.restart_service()
            restart.assert_awaited_once()
        self.assertFalse(b.restarting)
        self.assertEqual(b.state["inbox"], [job])

    async def test_submitted_work_still_refuses_restart(self):
        for phase in (InputPhase.DISPATCHING, InputPhase.ACCEPTED):
            self.bridge.state["currentInput"] = self.job(phase)
            with self.assertRaises(UserFacingError):
                await self.bridge.restart_service()

    def test_dispatch_barrier_preserves_stable_job_for_retry(self):
        b = self.bridge
        job = self.job()
        b.state["currentInput"] = job
        b.restarting = True
        self.assertFalse(b.dispatch_input(job, InputPhase.DISPATCHING, None, b.backend))
        b.restarting = False  # A restart failure must not discard the paused input.
        b.inputs.defer_restart(job)
        self.assertEqual(b.state["inbox"], [job])
        self.assertNotIn("currentInput", b.state)
        self.assertEqual(job["phase"], InputPhase.PREPARING)
        self.assertNotIn("restartDeferred", job)

    async def test_restart_during_session_preparation_does_not_submit(self):
        b = self.bridge
        b.state["inbox"] = [self.job()]
        entered, release, deferred = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def prepare(_):
            entered.set()
            await release.wait()
        async def owner():
            release.set()
            await deferred.wait()
        original = b.inputs.defer_restart
        def defer(job):
            original(job)
            deferred.set()
        b.inputs.defer_restart = defer
        b.reply = lambda *_: None
        b.backend.run = AsyncMock()
        async def abort():
            b.input_paused = True
            raise RuntimeError("simulated restart failure")
        with patch("input_delivery.ensure_session", prepare), \
             patch("agent_wechat.service_control.require_service_owner", owner), \
             patch("agent_wechat.service_control.restart", abort):
            task = asyncio.create_task(b.worker())
            try:
                await asyncio.wait_for(entered.wait(), 1)
                with self.assertRaises(RuntimeError):
                    await asyncio.wait_for(b.restart_service(), 1)
                b.backend.run.assert_not_awaited()
                self.assertEqual([job["clientId"] for job in b.state["inbox"]], ["stable"])
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


class DaemonRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def transport(self):
        return CodexTransport({"command": ["codex-deepseek", "app-server"]}, "/tmp",
            on_request=AsyncMock(), on_event=lambda _: None,
            on_connect=lambda: None, on_disconnect=lambda _: None)

    async def test_active_launcher_is_reused_without_spawning(self):
        transport = self.transport()
        with patch("codex_transport.shutil.which", return_value="/bin/codex-deepseek"), \
             patch("daemon_control.status", AsyncMock(return_value={"ActiveState": "active"})), \
             patch("codex_transport.asyncio.create_subprocess_exec", AsyncMock()) as spawn:
            one = await transport.start_shared_daemon(["codex-deepseek", "app-server"], {"HOME": "/tmp"})
            two = await transport.start_shared_daemon(["codex-deepseek", "app-server"], {"HOME": "/tmp"})
        self.assertEqual(one, two)
        spawn.assert_not_awaited()

    async def test_keyring_failure_is_explicit_and_has_cooldown(self):
        transport = self.transport()
        state = {"ActiveState": "failed", "Result": "exit-code", "ExecMainStatus": "78"}
        transport.connect = AsyncMock(side_effect=RuntimeError("connection failed"))
        transport.close = AsyncMock()
        transport.start_shared_daemon = AsyncMock(return_value="unit.service")
        with patch("daemon_control.status", AsyncMock(return_value=state)):
            with self.assertRaises(BackendStartupError) as caught:
                await transport.start()
        self.assertEqual(caught.exception.retry_after, 60)
        self.assertIn("密钥环", backend_reason(caught.exception))
        other = daemon_control.failure(state, ["codex", "app-server"])
        self.assertNotIn("DeepSeek", str(other))
