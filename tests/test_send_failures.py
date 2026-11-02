"""Channel recovery counter is independent of retained delivery history."""
import asyncio
from pathlib import Path
import tempfile
import unittest

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
            self.assertEqual(bridge.prompt_fields()["发送失败数"], 1)
            self.assertEqual(self.store.load("bridge")["consecutiveSendFailures"], 1)
            self.assertEqual(len(bridge.state["failedOutbox"]), 1)
            release.set()
            await asyncio.wait_for(bridge.outbox_drained.wait(), 1)
            self.assertEqual(bridge.prompt_fields()["发送失败数"], 0)
            self.assertEqual(self.store.load("bridge")["consecutiveSendFailures"], 0)
            self.assertEqual(len(bridge.state["failedOutbox"]), 1)
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
        self.assertEqual(bridge.prompt_fields()["发送失败数"], 25)
        restore(bridge.state, {"thread": "other"})
        bridge.backend_name = "deepseek"
        bridge.save()
        restarted = Bridge(self.store, {"user": "owner"}, {"backends": {"deepseek": {"command": ["fake-deepseek", "app-server"]}}})
        self.assertEqual(restarted.prompt_fields()["发送失败数"], 25)
        self.assertEqual(len(restarted.state["failedOutbox"]), 20)

    async def test_legacy_history_is_preserved_without_inventing_a_counter(self):
        state = self.bridge.state
        state.pop("consecutiveSendFailures")
        state["failedOutbox"] = [{"id": "historical"}]
        self.store.save("bridge", state)
        restored = Bridge(self.store, {"user": "owner"}, {"backends": {"deepseek": {"command": ["fake-deepseek", "app-server"]}}})
        self.assertEqual(restored.prompt_fields()["发送失败数"], 0)
        self.assertEqual(restored.state["failedOutbox"], [{"id": "historical"}])
