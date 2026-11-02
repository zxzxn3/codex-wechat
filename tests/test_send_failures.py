"""Channel recovery counter is independent of retained delivery history."""
import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from codex_wechat import Bridge
from profiles import restore
from storage import Store


class SendFailureTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "state")
        self.bridge = Bridge(self.store, {"user": "owner"}, {"backends": {"deepseek": {"command": ["fake-deepseek", "app-server"]}}})
        self.bridge.state["context"] = "context"

    def tearDown(self):
        self.store.lock.close()
        self.temp.cleanup()

    async def test_exhausted_retry_counts_until_confirmed_send_and_keeps_history(self):
        bridge = self.bridge
        bridge.reply("answer")
        bridge.state["outbox"][0]["attempts"] = 2
        reached, release = asyncio.Event(), asyncio.Event()
        calls = 0
        async def send(_):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("simulated channel failure")
            reached.set()
            await release.wait()
        bridge.channel.send = send
        task = asyncio.create_task(bridge.sender())
        try:
            await asyncio.wait_for(reached.wait(), 1)
            self.assertEqual(bridge.prompt_fields()["send_failures"], 1)
            self.assertEqual(self.store.load("bridge")["consecutiveSendFailures"], 1)
            self.assertEqual(len(bridge.state["failedOutbox"]), 1)
            release.set()
            await asyncio.wait_for(bridge.outbox_drained.wait(), 1)
            self.assertEqual(bridge.prompt_fields()["send_failures"], 0)
            self.assertEqual(self.store.load("bridge")["consecutiveSendFailures"], 0)
            self.assertEqual(len(bridge.state["failedOutbox"]), 1)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_retry_diagnostic_matches_configured_wait(self):
        bridge = self.bridge
        bridge.reply("answer")
        calls = 0

        async def send(_):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("simulated channel failure")

        bridge.channel.send = send
        with (patch("output_delivery.log_event") as log_event,
              patch("output_delivery.SEND_RETRY_SECONDS", 0)):
            task = asyncio.create_task(bridge.sender())
            try:
                await asyncio.wait_for(bridge.outbox_drained.wait(), 1)
                failed = next(call for call in log_event.call_args_list
                              if call.args and call.args[0] == "output.send_failed")
                self.assertEqual(failed.kwargs["retry_in_ms"], 0)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_failure_count_is_not_capped_or_reset_by_session_or_restart(self):
        bridge = self.bridge
        for index in range(25):
            entry = bridge.text_entries(str(index), group=str(index), context="context")[0]
            entry["failureNotice"] = True
            bridge.enqueue([entry])
            bridge.fail_output(entry)
        self.assertEqual(len(bridge.state["failedOutbox"]), 20)
        self.assertEqual(bridge.prompt_fields()["send_failures"], 25)
        restore(bridge.state, {"thread": "other"})
        bridge.backend_name = "deepseek"
        bridge.save()
        restarted = Bridge(self.store, {"user": "owner"}, {"backends": {"deepseek": {"command": ["fake-deepseek", "app-server"]}}})
        self.assertEqual(restarted.prompt_fields()["send_failures"], 25)
        self.assertEqual(len(restarted.state["failedOutbox"]), 20)

    async def test_legacy_history_is_preserved_without_inventing_a_counter(self):
        state = self.bridge.state
        state.pop("consecutiveSendFailures")
        state["failedOutbox"] = [{"id": "historical"}]
        self.store.save("bridge", state)
        restored = Bridge(self.store, {"user": "owner"}, {"backends": {"deepseek": {"command": ["fake-deepseek", "app-server"]}}})
        self.assertEqual(restored.prompt_fields()["send_failures"], 0)
        self.assertEqual(restored.state["failedOutbox"], [{"id": "historical"}])
