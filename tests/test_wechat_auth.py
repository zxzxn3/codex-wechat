"""Shared WeChat authentication state and identity-change policy."""
import unittest

import wechat_auth


class WeChatAuthTests(unittest.TestCase):
    def test_same_user_rebind_preserves_work_but_resets_transport(self):
        current = {"id": "active", "phase": "accepted"}
        state = {"context": "old", "cursor": "old", "seen": ["seen"],
                 "currentInput": current, "inbox": [{"id": "queued"}],
                 "outbox": [{"id": "reply", "context": "old", "attempts": 2,
                             "item": {"file": "old-upload"}, "asset": {"path": "local"}}]}
        wechat_auth.prepare_rebind(state, {"user": "u", "bot": "old"},
                                   {"user": "u", "bot": "new"})
        self.assertIs(state["currentInput"], current)
        self.assertEqual(state["inbox"], [{"id": "queued"}])
        self.assertEqual(state["seen"], ["seen"])
        self.assertEqual(state["cursor"], "")
        self.assertNotIn("context", state)
        self.assertTrue(state["wechatContextPending"])
        self.assertEqual(state["outbox"], [{"id": "reply", "context": "",
                                           "asset": {"path": "local"}}])
        wechat_auth.accept_context(state, "fresh")
        self.assertEqual(state["outbox"][0]["context"], "fresh")
        self.assertNotIn("wechatContextPending", state)

    def test_different_user_still_quarantines_and_clears_context_wait(self):
        state = {"wechatContextPending": True, "outbox": [{"id": "reply"}],
                 "inbox": [{"id": "queued"}]}
        wechat_auth.prepare_rebind(state, {"user": "old"}, {"user": "new"})
        self.assertEqual(state["outbox"], [])
        self.assertEqual(state["inbox"], [])
        self.assertNotIn("wechatContextPending", state)

    def test_reauth_state_is_saved_once_and_preserves_first_detection(self):
        state = {}
        self.assertTrue(wechat_auth.require_rebind(state, pending=2, now=100))
        first = dict(state["wechatAuth"])
        self.assertFalse(wechat_auth.require_rebind(state, pending=9, now=200))
        self.assertEqual(state["wechatAuth"], first)
        self.assertEqual(first["state"], wechat_auth.AUTH_REQUIRED)
        self.assertEqual(first["unsent"], 2)

    def test_mark_valid_is_idempotent_for_same_identity(self):
        state = {}
        account = {"bot": "bot-a", "user": "user-a"}
        self.assertTrue(wechat_auth.mark_valid(state, account, now=100))
        saved = dict(state["wechatAuth"])
        self.assertFalse(wechat_auth.mark_valid(state, account, now=200))
        self.assertEqual(state["wechatAuth"], saved)
        self.assertEqual(wechat_auth.state(state), wechat_auth.AUTH_VALID)

    def test_identity_change_quarantines_old_chat_without_retargeting(self):
        state = {
            "cursor": "old-cursor",
            "seen": ["old-message"],
            "context": "old-context",
            "thread": "old-thread",
            "backendSessions": {"codex": {"thread": "old-thread"}},
            "currentInput": {"id": "current"},
            "inbox": [{"id": "queued"}],
            "outbox": [{"id": "unsent", "user": "old-user"}],
            "failedOutbox": [{"id": "historical"}],
            "processingNotice": "old ack",
            "completionNotice": "old done",
            "sessionChoices": ["old-thread"],
        }
        counts = wechat_auth.prepare_identity_change(
            state, {"bot": "new-bot", "user": "new-user"}
        )
        self.assertEqual(counts, {"inputs": 2, "outbox": 1})
        self.assertEqual(state["cursor"], "")
        self.assertEqual(state["seen"], [])
        self.assertNotIn("context", state)
        self.assertEqual(state["thread"], "old-thread")
        self.assertEqual(state["backendSessions"], {"codex": {"thread": "old-thread"}})
        self.assertEqual(state["processingNotice"], "old ack")
        self.assertEqual(state["completionNotice"], "old done")
        self.assertEqual(state["sessionChoices"], ["old-thread"])
        self.assertNotIn("currentInput", state)
        self.assertEqual(state["inbox"], [])
        self.assertEqual(state["outbox"], [])
        self.assertEqual([item["id"] for item in state["inputRecords"]],
                         ["current", "queued"])
        self.assertTrue(all(item["phase"] == wechat_auth.IDENTITY_CHANGED
                            for item in state["inputRecords"]))
        self.assertEqual([item["id"] for item in state["failedOutbox"]],
                         ["historical", "unsent"])
        self.assertEqual(state["failedOutbox"][-1]["user"], "old-user")
        self.assertEqual(state["wechatAuth"]["user"], "new-user")


if __name__ == "__main__":
    unittest.main()
