"""Durable control intent across the save/enqueue crash boundary."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_wechat import Bridge
from commands import command_restart
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
        with patch.object(bridge, "save", wraps=bridge.save) as save:
            bridge.restore_controls()
        save.assert_called_once()
        self.assertTrue(bridge.controls["urgent"].empty())
        self.assertIn("可能未完成", bridge.state["outbox"][-1]["text"])
        self.assertEqual(bridge.state["pendingControls"], [])
        saved = store.load("bridge")
        self.assertIn("可能未完成", saved["outbox"][-1]["text"])
        self.assertEqual(saved["pendingControls"], [])
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

    async def test_cancelling_a_running_control_keeps_its_recovery_marker(self):
        store = Store(self.root / "state")
        bridge = Bridge(store, self.account, self.config)
        control = {
            "id": "cancel-control",
            "text": "/help",
            "received": {"request_id": "cancel-control", "controlId": "cancel-control"},
            "state": "queued",
            "lane": "local",
        }
        bridge.state["pendingControls"] = [control]
        self.assertTrue(bridge.enqueue_control(control))
        entered = asyncio.Event()

        async def block(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()

        bridge.control = block
        task = asyncio.create_task(bridge.control_worker("local"))
        await entered.wait()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(bridge.state["pendingControls"][0]["state"], "running")
        store.close()

    async def test_restore_accepts_every_pending_control_in_one_lane(self):
        store = Store(self.root / "state")
        bridge = Bridge(store, self.account, self.config)
        bridge.state["pendingControls"] = [{
            "id": f"control-{index}",
            "text": "/help",
            "received": {"controlId": f"control-{index}"},
            "state": "queued",
            "lane": "local",
        } for index in range(21)]
        bridge.restore_controls()
        self.assertEqual(bridge.controls["local"].qsize(), 21)
        self.assertEqual(len(bridge.state["pendingControls"]), 21)
        store.close()

    async def test_restart_keeps_a_recovery_marker_until_the_service_restarts(self):
        store = Store(self.root / "state")
        bridge = Bridge(store, self.account, self.config)
        bridge.state["pendingControls"] = [{
            "id": "restart-control",
            "text": "/restart",
            "received": {"controlId": "restart-control"},
            "state": "running",
            "lane": "local",
        }]
        async def restart_then_interrupt():
            bridge.state["restartRequested"] = True
            bridge.save()
            raise KeyboardInterrupt

        with patch.object(bridge, "restart_service", restart_then_interrupt):
            with self.assertRaises(KeyboardInterrupt):
                await command_restart(
                    bridge, "/restart", "",
                    {"controlId": "restart-control"},
                )
        self.assertEqual(bridge.state["pendingControls"][0]["state"], "restarting")
        store.close()

        store = Store(self.root / "state")
        restarted = Bridge(store, self.account, self.config)
        self.assertTrue(restarted.confirm_restart())
        self.assertEqual(restarted.state["pendingControls"], [])
        store.close()

    async def test_an_unconfirmed_restart_is_reported_instead_of_replayed(self):
        store = Store(self.root / "state")
        store.save("bridge", {
            "context": "ctx",
            "pendingControls": [{
                "id": "restart-control",
                "text": "/restart",
                "received": {"controlId": "restart-control"},
                "state": "restarting",
                "lane": "local",
            }],
        })
        bridge = Bridge(store, self.account, self.config)
        bridge.restore_controls()
        self.assertTrue(bridge.controls["local"].empty())
        self.assertIn("可能未完成", bridge.state["outbox"][-1]["text"])
        store.close()

    async def test_interrupted_notice_and_cleanup_share_one_save(self):
        store = Store(self.root / "state")
        store.save("bridge", {
            "context": "ctx",
            "pendingControls": [
                {
                    "id": "queued-control",
                    "text": "/help",
                    "received": {"controlId": "queued-control"},
                    "state": "queued",
                    "lane": "local",
                },
                {
                    "id": "running-control",
                    "text": "/answer 1 yes",
                    "received": {"controlId": "running-control"},
                    "state": "running",
                    "lane": "urgent",
                },
            ],
        })
        bridge = Bridge(store, self.account, self.config)
        with patch.object(bridge, "reply", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                bridge.restore_controls()
        store.close()

        store = Store(self.root / "state")
        saved = store.load("bridge")
        self.assertEqual(
            [control["state"] for control in saved["pendingControls"]],
            ["queued", "running"],
        )
        self.assertEqual(saved.get("outbox", []), [])
        store.close()
