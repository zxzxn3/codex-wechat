"""Durable control intent across the save/enqueue crash boundary."""
import asyncio
import tempfile
import unittest
from pathlib import Path

from codex_wechat import Bridge
from storage import Store


class ControlRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.account = {"user": "owner"}
        self.config = {"backends": {
            "codex": {}, "deepseek": {"command": ["fake-deepseek", "app-server"]}}}

    def tearDown(self):
        self.temp.cleanup()

    async def test_a_queued_control_is_recovered_after_the_save_enqueue_window(self):
        store = Store(self.root / "state")
        bridge = Bridge(store, self.account, self.config)
        original_save = bridge.save

        def save_then_interrupt():
            original_save()
            bridge.save = original_save
            raise KeyboardInterrupt

        bridge.save = save_then_interrupt
        message = {
            "message_id": "recover-control",
            "from_user_id": "owner",
            "message_type": 1,
            "context_token": "ctx",
            "item_list": [{"type": 1, "text_item": {"text": "/help"}}],
        }
        with self.assertRaises(KeyboardInterrupt):
            await bridge.receive({"msgs": [message]})
        saved = store.load("bridge")
        control_id = saved["pendingControls"][0]["id"]
        self.assertEqual(saved["pendingControls"][0]["state"], "queued")
        store.close()

        store = Store(self.root / "state")
        restarted = Bridge(store, self.account, self.config)
        restarted.restore_controls()
        text, received = restarted.controls["local"].get_nowait()
        self.assertEqual(text, "/help")
        self.assertEqual(received["controlId"], control_id)
        self.assertEqual(restarted.state["pendingControls"][0]["state"], "queued")
        store.close()

    async def test_a_running_control_is_reported_instead_of_replayed(self):
        store = Store(self.root / "state")
        store.save("bridge", {
            "context": "ctx",
            "pendingControls": [{
                "id": "running-control",
                "text": "/answer 1 yes",
                "received": {"request_id": "running-control", "controlId": "running-control"},
                "state": "running",
                "lane": "urgent",
            }],
        })
        bridge = Bridge(store, self.account, self.config)
        bridge.restore_controls()
        self.assertTrue(bridge.controls["urgent"].empty())
        self.assertIn("可能未完成", bridge.state["outbox"][-1]["text"])
        self.assertEqual(bridge.state["pendingControls"], [])
        store.close()

    async def test_a_completed_control_is_removed_from_the_pending_state(self):
        store = Store(self.root / "state")
        bridge = Bridge(store, self.account, self.config)
        bridge.state["context"] = "ctx"
        control = {
            "id": "complete-control",
            "text": "/help",
            "received": {"request_id": "complete-control", "controlId": "complete-control"},
            "state": "queued",
            "lane": "local",
        }
        bridge.state["pendingControls"] = [control]
        self.assertTrue(bridge.enqueue_control(control))
        task = asyncio.create_task(bridge.control_worker("local"))
        try:
            await asyncio.wait_for(bridge.controls["local"].join(), 1)
            self.assertEqual(bridge.state["pendingControls"], [])
            self.assertTrue(bridge.state["outbox"])
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        store.close()
