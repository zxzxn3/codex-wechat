"""Binding replacement must not transfer old WeChat delivery across identities."""
import copy
import unittest
from binding import commit_binding
from codex_wechat import startup_account
from configuration import ConfigurationError

class Store:
    def __init__(self):
        self.data = {"account": {"bot": "old", "user": "owner", "token": "fixture"},
                     "bridge": {"thread": "chosen", "backendName": "codex", "processingNotice": "compact",
                                "outbox": ["private old reply"], "inbox": ["old input"],
                                "seen": ["old"], "currentInput": {"phase": "accepted"}}}
        self.fail_final = False
    def load(self, name, default=None):
        return copy.deepcopy(self.data.get(name, default))
    def save(self, name, value):
        if self.fail_final and name == "account" and value.get("token"):
            raise OSError("simulated disk failure")
        self.data[name] = copy.deepcopy(value)

class BindingTests(unittest.IsolatedAsyncioTestCase):
    async def test_changed_identity_requires_explicit_rebind(self):
        store=Store(); original=copy.deepcopy(store.data)
        with self.assertRaisesRegex(RuntimeError, "(?i)rebind"):
            commit_binding(store, {"bot":"new", "user":"owner", "token":"fixture-new"})
        self.assertEqual(store.data, original)
    async def test_rebind_preserves_preferences_but_discards_delivery(self):
        store=Store(); account={"bot":"new", "user":"owner", "token":"fixture-new"}
        commit_binding(store, account, rebind=True)
        state=store.data["bridge"]
        self.assertEqual(state["thread"], "chosen")
        self.assertEqual(state["processingNotice"], "compact")
        self.assertEqual(state["inbox"], [])
        self.assertEqual(state["outbox"], [])
        self.assertNotIn("currentInput", state)
        self.assertEqual(store.data["account"], account)
    async def test_rotated_token_does_not_reset_same_identity(self):
        store=Store(); previous=copy.deepcopy(store.data["bridge"])
        commit_binding(store, {"bot":"old", "user":"owner", "token":"rotated"})
        self.assertEqual(store.data["bridge"], previous)
    async def test_partial_commit_cannot_start_a_bridge(self):
        store=Store(); store.fail_final=True
        with self.assertRaises(OSError):
            commit_binding(store, {"bot":"new", "user":"owner", "token":"fixture-new"}, rebind=True)
        with self.assertRaisesRegex(ConfigurationError, "(?i)rebind"):
            await startup_account(store)
        store.fail_final=False
        commit_binding(store, {"bot":"new", "user":"owner", "token":"fixture-new"}, rebind=True)
        self.assertFalse(store.data["account"].get("bindingPending", False))
