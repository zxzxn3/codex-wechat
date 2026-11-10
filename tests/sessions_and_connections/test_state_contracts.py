"""Persistent bridge-state invariants and save-boundary crash probes."""
import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from codex_wechat.app import BRIDGE_STATE_DEFAULTS, Bridge
from codex_wechat.command_specs import PENDING_CONTROL_LIMIT
from codex_wechat.errors import WeChatNotAcknowledged
from codex_wechat.records import InputPhase
from codex_wechat.storage import Store


INBOX_LIMIT = 20
FAILED_OUTBOX_LIMIT = 20
SEEN_LIMIT = 2000
CONTROL_STATES = {"queued", "running", "restarting"}


class SimulatedCrash(BaseException):
    """A process death, not an exception recoverable by the bridge."""


class StateContractTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.store = Store(self.base / "state")
        self.addCleanup(self.store.close)
        self.account = {"user": "owner"}
        self.config = {"backends": {
            "codex": {}, "deepseek": {"command": ["fake-deepseek", "app-server"]}}}
        self.bridge = Bridge(self.store, self.account, self.config)
        self.bridge.state["context"] = "context"

    def assert_contract(self, state, *, bridge=None):
        for key in BRIDGE_STATE_DEFAULTS:
            self.assertIn(key, state)
        for key in ("inbox", "outbox", "seen", "pendingControls"):
            self.assertIsInstance(state[key], list, key)
        self.assertEqual(state["version"], 1)

        self.assertLessEqual(len(state["inbox"]), INBOX_LIMIT)
        self.assertLessEqual(len(state["pendingControls"]), PENDING_CONTROL_LIMIT)
        self.assertLessEqual(len(state.get("failedOutbox", [])), FAILED_OUTBOX_LIMIT)
        self.assertLessEqual(len(state["seen"]), SEEN_LIMIT)
        self.assertEqual(len(state["seen"]), len(set(state["seen"])))

        current = state.get("currentInput")
        inbox_ids = [job.get("id") for job in state["inbox"]]
        self.assertEqual(len(inbox_ids), len(set(inbox_ids)))
        if current:
            self.assertNotIn(current.get("id"), inbox_ids)
            self.assertFalse(any(job is current for job in state["inbox"]))

        ids = [entry.get("id") for entry in state["outbox"]]
        self.assertTrue(all(isinstance(identity, str) and identity for identity in ids))
        self.assertEqual(len(ids), len(set(ids)))

        control_ids = [control.get("id") for control in state["pendingControls"]]
        self.assertTrue(all(isinstance(identity, str) and identity
                            for identity in control_ids))
        self.assertEqual(len(control_ids), len(set(control_ids)))
        self.assertTrue(all(isinstance(control, dict) for control in state["pendingControls"]))
        self.assertTrue(all(control.get("state") in CONTROL_STATES
                            for control in state["pendingControls"]))
        for control in state["pendingControls"]:
            received = control.get("received")
            self.assertIsInstance(received, dict)
            if "controlId" in received:
                self.assertEqual(received["controlId"], control["id"])

        self.assertIs(type(state["deliveryUnknownTotal"]), int)
        self.assertGreaterEqual(state["deliveryUnknownTotal"], 0)
        unknown_retained = sum(
            1 for entry in state.get("failedOutbox", [])
            if entry.get("status") == "delivery_unknown"
        )
        self.assertGreaterEqual(state["deliveryUnknownTotal"], unknown_retained)

        phases = {str(phase) for phase in InputPhase}
        jobs = ([current] if current else []) + state["inbox"]
        for job in jobs:
            if job.get("phase") is not None:
                self.assertIn(str(job["phase"]), phases)

        if bridge is not None:
            self.assertEqual(bridge.outbox_drained.is_set(), not state["outbox"])

    def assert_persisted_contract(self):
        self.assert_contract(self.store.load("bridge"), bridge=self.bridge)

    def message(self, identity, text):
        return {
            "message_id": identity,
            "from_user_id": "owner",
            "message_type": 1,
            "context_token": "context",
            "item_list": [{"type": 1, "text_item": {"text": text}}],
        }

    def job(self, identity, phase=InputPhase.PREPARING):
        return {
            "id": identity,
            "clientId": "client-" + identity,
            "text": identity,
            "attachments": [],
            "mode": "steer",
            "phase": phase,
        }

    def crashing_save(self, bridge, boundary):
        original = bridge.save

        def save():
            if boundary == "before":
                raise SimulatedCrash(boundary)
            original()
            raise SimulatedCrash(boundary)

        return patch.object(bridge, "save", side_effect=save)

    async def test_receive_persists_a_consistent_input_and_control_snapshot(self):
        await self.bridge.receive({"get_updates_buf": "cursor", "msgs": [
            self.message("control", "/help"),
            self.message("queued", "/queue requested work"),
            self.message("live", "follow-up"),
        ]})
        self.assert_contract(self.bridge.state, bridge=self.bridge)
        self.assert_persisted_contract()
        self.assertEqual(len(self.bridge.state["pendingControls"]), 1)
        self.assertEqual(len(self.bridge.state["inbox"]), 2)
        self.assertEqual(self.bridge.state["cursor"], "cursor")

    async def test_a_control_saved_before_or_after_its_running_marker_is_never_lost(self):
        control = {
            "id": "control",
            "text": "/help",
            "received": {"request_id": "control", "controlId": "control"},
            "state": "queued",
            "lane": "local",
        }
        for boundary in ("before", "after"):
            with self.subTest(boundary=boundary):
                store = Store(self.base / ("control-" + boundary))
                bridge = Bridge(store, self.account, self.config)
                bridge.state["context"] = "context"
                bridge.state["pendingControls"] = [dict(control)]
                bridge.save()

                with self.crashing_save(bridge, boundary):
                    with self.assertRaises(SimulatedCrash):
                        bridge.mark_control_running("control")
                saved = store.load("bridge")
                self.assert_contract(saved)
                self.assertEqual(saved["pendingControls"][0]["state"],
                                 "queued" if boundary == "before" else "running")

                restarted = Bridge(store, self.account, self.config)
                restarted.restore_controls()
                self.assert_contract(restarted.state, bridge=restarted)
                self.assertIsNotNone(store.load("bridge"))
                if boundary == "before":
                    text, received = restarted.controls["local"].get_nowait()
                    self.assertEqual(text, "/help")
                    self.assertEqual(received["controlId"], "control")
                else:
                    self.assertTrue(restarted.controls["local"].empty())
                    self.assertIn("可能未完成", restarted.state["outbox"][-1]["text"])
                store.close()

    async def test_input_recovery_keeps_exactly_one_job_at_both_handoff_snapshots(self):
        for snapshot in ("inbox", "currentInput"):
            with self.subTest(snapshot=snapshot):
                store = Store(self.base / ("input-" + snapshot))
                job = self.job("job")
                saved = {"context": "context", "inbox": []}
                if snapshot == "inbox":
                    saved["inbox"] = [job]
                else:
                    saved["currentInput"] = job
                store.save("bridge", saved)

                bridge = Bridge(store, self.account, self.config)
                bridge.recover_inputs()
                self.assert_contract(bridge.state, bridge=bridge)
                self.assert_contract(store.load("bridge"), bridge=bridge)
                self.assertEqual([item["id"] for item in bridge.state["inbox"]], ["job"])
                self.assertNotIn("currentInput", bridge.state)
                store.close()

    async def test_successful_send_persists_a_consistent_empty_outbox(self):
        self.bridge.reply("one")
        self.bridge.channel.send = lambda entry: asyncio.sleep(0, result={"message_id": "m"})
        task = asyncio.create_task(self.bridge.sender())
        try:
            await asyncio.wait_for(self.bridge.outbox_drained.wait(), 1)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.assert_contract(self.bridge.state, bridge=self.bridge)
        self.assert_persisted_contract()
        self.assertEqual(self.bridge.state["outbox"], [])
        self.assertEqual(self.bridge.state["consecutiveSendFailures"], 0)

    async def test_no_id_pause_is_runtime_only_and_keeps_the_backlog(self):
        self.bridge.reply("one")
        self.bridge.reply("two")

        async def send(_):
            raise WeChatNotAcknowledged(0)

        self.bridge.channel.send = send
        with patch("codex_wechat.output_delivery.log_event"), self.crashing_save(self.bridge, "after"):
            with self.assertRaises(SimulatedCrash):
                await self.bridge.sender()

        self.assertTrue(self.bridge.output.paused)
        saved = self.store.load("bridge")
        self.assert_contract(saved)
        self.assertNotIn("deliveryPaused", saved)
        self.assertEqual(saved["deliveryUnknownTotal"], 1)
        self.assertEqual(saved["failedOutbox"][-1]["status"], "delivery_unknown")
        self.assertEqual([entry["text"] for entry in saved["outbox"]], ["two"])

        restarted = Bridge(self.store, self.account, self.config)
        restarted.refresh_reply_budget()
        self.assert_contract(restarted.state, bridge=restarted)
        self.assertFalse(restarted.output.paused)
        self.assertEqual([entry["text"] for entry in restarted.state["outbox"]], ["two"])
        self.assert_persisted_contract()

    async def test_a_crash_before_the_no_id_save_leaves_the_uncertain_payload_queued(self):
        # A process can die before its first durable delivery-unknown marker. The
        # next process cannot distinguish that from "not sent", so the payload is
        # replayed; this is the documented at-least-once boundary, not a success.
        self.bridge.reply("one")

        async def send(_):
            raise WeChatNotAcknowledged(0)

        self.bridge.channel.send = send
        with patch("codex_wechat.output_delivery.log_event"), self.crashing_save(self.bridge, "before"):
            with self.assertRaises(SimulatedCrash):
                await self.bridge.sender()

        saved = self.store.load("bridge")
        self.assert_contract(saved)
        self.assertNotIn("deliveryPaused", saved)
        self.assertEqual([entry["text"] for entry in saved["outbox"]], ["one"])
        self.assertEqual(saved["deliveryUnknownTotal"], 0)


if __name__ == "__main__":
    unittest.main()
