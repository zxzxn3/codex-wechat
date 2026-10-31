import asyncio
import os
from pathlib import Path
import sys
import tempfile
import unittest

from agent_wechat import Bridge
from backend import CodexBackend, CodexRpcError
from media import MediaError
from storage import Store
from wechat import trusted_url


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.bridge = Bridge(self.store, {"user": "owner"}, {})

    def tearDown(self):
        self.store.lock.close()
        self.temp.cleanup()

    def message(self, identity="1", user="owner", text="hello"):
        return {"message_id": identity, "from_user_id": user, "message_type": 1,
                "context_token": "context", "item_list": [{"type": 1,
                                                           "text_item": {"text": text}}]}

    async def test_identity_group_filter_and_cursor_dedup(self):
        group = self.message("3")
        group["group_id"] = "group"
        await self.bridge.receive({"msgs": [self.message(), self.message(),
            self.message("2", "stranger"), group], "get_updates_buf": "next"})
        saved = self.store.load("bridge")
        self.assertEqual([x["id"] for x in saved["inbox"]], ["1"])
        self.assertEqual(saved["cursor"], "next")
        restarted = Bridge(self.store, {"user": "owner"}, {})
        await restarted.receive({"msgs": [self.message()]})
        self.assertEqual(len(restarted.state["inbox"]), 1)

    async def test_no_context_no_execution_and_stop_clears_queue(self):
        message = self.message()
        del message["context_token"]
        await self.bridge.receive({"msgs": [message]})
        self.assertFalse(self.bridge.state["inbox"])
        await self.bridge.receive({"msgs": [self.message("2")]})
        await self.bridge.control("/stop")
        self.assertFalse(self.store.load("bridge")["inbox"])

    async def test_private_state_and_utf8_chunks(self):
        self.bridge.state["context"] = "context"
        self.bridge.reply("汉" * 2500)
        entries = self.store.load("bridge")["outbox"]
        self.assertEqual("".join(x["text"] for x in entries), "汉" * 2500)
        self.assertTrue(all(len(x["text"].encode()) <= 3000 for x in entries))
        self.assertEqual(os.stat(Path(self.temp.name) / "bridge.json").st_mode & 0o777, 0o600)

    async def test_stdio_backend_final_only_and_resume(self):
        backend = CodexBackend({"command": [sys.executable,
            str(Path(__file__).with_name("fake_server.py"))]}, self.temp.name, lambda _: None)
        try:
            await backend.start()
            thread = await backend.thread()
            self.assertEqual(await backend.thread(thread), thread)
            answer = await asyncio.wait_for(backend.run(thread, "hello"), 3)
            self.assertEqual(answer, "hello")
        finally:
            await backend.close()

    async def test_credential_redirect_boundary(self):
        for value in ("http://ilinkai.weixin.qq.com", "https://evil.example",
                      "https://ilinkai.weixin.qq.com.evil.example", "https://u@ilinkai.weixin.qq.com"):
            with self.assertRaises(RuntimeError):
                trusted_url(value)
        self.assertEqual(trusted_url("https://ilinkai.weixin.qq.com/"), "https://ilinkai.weixin.qq.com")




    async def test_stop_during_input_preflight_never_starts_turn(self):
        backend = CodexBackend({"sharedDaemon": False}, self.temp.name, lambda _: None)
        preparing, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def inputs(*args):
            preparing.set()
            await release.wait()
            return []

        async def rpc(method, params):
            calls.append(method)
            raise AssertionError("No RPC should run after stopping preflight")

        backend.input_items, backend.rpc = inputs, rpc
        task = asyncio.create_task(backend.run("thread", "image task"))
        await preparing.wait()
        self.assertTrue(await backend.stop())
        release.set()
        self.assertIn("未启动", await task)
        self.assertEqual(calls, [])

    async def test_desktop_events_deliver_once_without_owning_turn(self):
        backend = self.bridge.backend
        backend.selected_thread = "desktop-thread"
        results = []
        backend.on_external_result = lambda *args: results.append(args)
        backend.event({"method": "turn/started", "params": {"threadId": "desktop-thread",
                       "turn": {"id": "desktop-turn"}}})
        self.assertFalse(backend.active["owned"])
        backend.event({"method": "item/completed", "params": {"threadId": "desktop-thread",
            "turnId": "desktop-turn", "item": {"type": "agentMessage", "phase": "final_answer",
                                                  "text": "desktop result"}}})
        done = {"method": "turn/completed", "params": {"threadId": "desktop-thread",
                "turn": {"id": "desktop-turn", "status": "completed"}}}
        backend.event(done)
        backend.event(done)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][1], "desktop result")
        self.assertIsNone(backend.active)

    async def test_definite_steer_rejection_preserves_active_turn(self):
        backend = self.bridge.backend
        current = backend.new_turn("thread", "review-turn", owned=False)
        backend.active = current

        async def reject(*args):
            raise CodexRpcError(-32600, "active turn is not steerable")

        backend.rpc = reject
        self.assertFalse(await backend.steer("thread", "review-turn", "follow-up"))
        self.assertIs(backend.active, current)
        self.assertFalse(current["done"].done())

    async def test_automatic_media_cannot_read_outside_workspace(self):
        workspace = Path(self.temp.name) / "workspace"
        workspace.mkdir()
        with tempfile.TemporaryDirectory() as sibling:
            outside = Path(sibling) / "private.txt"
            outside.write_text("private")
            with self.assertRaises(MediaError):
                self.bridge.media.snapshot(str(outside), workspace, automatic=True)
            # An explicit user /send remains a separate, intentional operation.
            asset = self.bridge.media.snapshot(str(outside), workspace)
            self.assertEqual(Path(asset["path"]).read_text(), "private")
