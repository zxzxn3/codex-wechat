"""Independent regressions for the review findings; synthetic state only."""
import asyncio
import unittest

from codex_wechat.subprocess_io import communicate
from codex_wechat import wechat_auth


class WeChatIdentityRegressionTests(unittest.TestCase):
    def test_context_is_removed_without_dropping_user_preferences(self):
        state = {
            "context": "old-wechat-token",
            "cursor": "old-cursor",
            "seen": ["old-message"],
            "thread": "selected",
            "backendName": "deepseek",
            "backendSessions": {"deepseek": {"thread": "selected"}},
            "processingNotice": "kept",
            "completionNotice": "done",
            "sessionChoices": ["selected"],
            "currentInput": {"id": "current"},
            "inbox": [{"id": "queued"}],
            "outbox": [{"id": "reply"}],
        }

        wechat_auth.prepare_identity_change(
            state, {"bot": "new-bot", "user": "new-user"}
        )

        self.assertNotIn("context", state)
        self.assertEqual(state["cursor"], "")
        self.assertEqual(state["seen"], [])
        self.assertEqual(state["thread"], "selected")
        self.assertEqual(state["backendSessions"], {"deepseek": {"thread": "selected"}})
        self.assertEqual(state["processingNotice"], "kept")
        self.assertEqual(state["completionNotice"], "done")
        self.assertEqual(state["sessionChoices"], ["selected"])
        self.assertEqual(state["inbox"], [])
        self.assertEqual(state["outbox"], [])


class _BlockingProcess:
    def __init__(self):
        self.returncode = None
        self.stdin = None
        self.entered = asyncio.Event()
        self.exited = asyncio.Event()

    async def communicate(self, data):
        self.entered.set()
        await asyncio.Event().wait()

    def terminate(self):
        self.returncode = -15
        self.exited.set()

    async def wait(self):
        await self.exited.wait()
        return self.returncode


class SubprocessCancellationRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancellation_does_not_wait_for_the_abandoned_exchange(self):
        process = _BlockingProcess()
        task = asyncio.create_task(communicate(process, timeout=30))
        await process.entered.wait()

        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)

        self.assertEqual(process.returncode, -15)
        self.assertTrue(process.exited.is_set())


if __name__ == "__main__":
    unittest.main()
