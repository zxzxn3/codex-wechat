"""Detach target changes and future-turn setting updates."""
import asyncio
from dataclasses import asdict
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from codex_wechat.app import Bridge
from codex_wechat.backend import CodexBackend, DeferTurn, WRITER_NATIVE, WRITER_RELEASED
from codex_wechat.commands import handle
from codex_wechat.errors import UserFacingError
from codex_wechat.lifecycle import can_change_session
from codex_wechat.records import InputPhase, Source, TurnResult
from codex_wechat.storage import Store


class LiveSwitchingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "state")
        self.bridge = Bridge(self.store, {"user": "owner"}, {})
        self.bridge.state.update(context="context", thread="current")
        self.bridge.loaded = True
        self.bridge.ensure_backend = AsyncMock()
        self.bridge.backend.selected_thread = "current"

    def tearDown(self):
        self.store.lock.close()
        self.temp.cleanup()

    def job(self, identity, phase=InputPhase.ACCEPTED):
        return {"id": identity, "clientId": "client-" + identity, "text": identity,
                "phase": phase, "attachments": [], "mode": "steer"}

    def source(self, thread="current"):
        return Source(self.bridge.backend_name, "connection", 1, thread)

    def add_running_turn(self, *, owned=True):
        turn = self.bridge.backend.new_turn("current", "turn", owned=owned)
        self.bridge.backend.active = turn
        self.bridge.running = owned
        return turn

    async def test_detach_active_wakes_the_run_without_interrupting(self):
        backend = CodexBackend({}, "/tmp", lambda _: None)
        backend.rpc = AsyncMock(return_value={})
        current = backend.new_turn("thread", "turn", owned=True)
        backend.active = current

        self.assertTrue(await backend.detach_active())

        self.assertIsNone(backend.active)
        self.assertEqual(current["done"].result().status, "discarded")
        backend.rpc.assert_not_awaited()

    async def test_update_settings_uses_the_thread_settings_rpc(self):
        backend = CodexBackend({}, "/tmp", lambda _: None)
        backend.rpc = AsyncMock(return_value={})
        backend.selected_thread = "thread"
        backend.set_writer(WRITER_NATIVE, "thread")

        await backend.update_settings(
            "thread", model="model", effort="high", cwd="/workspace")

        backend.rpc.assert_awaited_once_with("thread/settings/update", {
            "threadId": "thread",
            "model": "model",
            "effort": "high",
            "cwd": "/workspace",
        })
        self.assertEqual(backend.info, {
            "model": "model", "effort": "high", "cwd": "/workspace"})

    async def test_update_settings_resumes_a_released_thread_first(self):
        backend = CodexBackend({}, "/tmp", lambda _: None)
        backend.selected_thread = "thread"
        backend.set_writer(WRITER_RELEASED, "thread")
        backend.thread = AsyncMock(return_value="thread")
        backend.rpc = AsyncMock(return_value={})
        backend.release_thread = AsyncMock(return_value=True)

        await backend.update_settings("thread", effort="high")

        backend.thread.assert_awaited_once_with("thread")
        backend.rpc.assert_awaited_once_with(
            "thread/settings/update", {"threadId": "thread", "effort": "high"})
        backend.release_thread.assert_awaited_once_with("thread", follow=True)

    async def test_output_discard_is_scoped_and_keeps_an_in_flight_entry(self):
        bridge = self.bridge
        bridge.state["outbox"] = [
            {"id": "old", "text": "old", "source": asdict(self.source())},
            {"id": "other", "text": "other", "source": asdict(self.source("other"))},
            {"id": "plain", "text": "plain"},
            {"id": "inflight", "text": "inflight", "source": asdict(self.source())},
        ]
        bridge.output._in_flight_id = "inflight"

        removed = bridge.discard_session_output(bridge.backend_name, "current")

        self.assertEqual(removed, 1)
        self.assertEqual([entry["id"] for entry in bridge.state["outbox"]],
                         ["other", "plain", "inflight"])

    async def test_backend_notice_is_tagged_with_the_active_source(self):
        bridge = self.bridge
        bridge.backend.notify("approval request")
        notice = bridge.state["outbox"][-1]
        self.assertEqual(notice["text"], "approval request")
        self.assertEqual(notice["source"], asdict(bridge.backend.source()))
        self.assertEqual(bridge.discard_session_output(bridge.backend_name, "current"), 1)

    async def test_switch_detaches_inputs_and_queued_output(self):
        bridge = self.bridge
        current = self.add_running_turn()
        bridge.state["currentInput"] = self.job("current")
        bridge.state["inbox"] = [self.job("queued", InputPhase.PREPARING)]
        bridge.state["outbox"] = [
            {"id": "old-output", "text": "old", "source": asdict(self.source())},
            {"id": "other-output", "text": "other", "source": asdict(self.source("other"))},
        ]
        bridge.backend.read_thread = AsyncMock(
            return_value={"status": {"type": "idle"}})
        target = "00000000-0000-0000-0000-000000000001"
        bridge.backend.thread = AsyncMock(return_value=target)
        bridge.backend.release_thread = AsyncMock(return_value=True)

        await handle(bridge, "/switch", target)

        self.assertEqual(bridge.state["thread"], target)
        self.assertEqual(bridge.state["inbox"], [])
        self.assertNotIn("currentInput", bridge.state)
        self.assertEqual(current["done"].result().status, "discarded")
        self.assertNotIn("old-output", [entry["id"] for entry in bridge.state["outbox"]])
        self.assertIn("other-output", [entry["id"] for entry in bridge.state["outbox"]])
        self.assertEqual([job["phase"] for job in bridge.state["inputRecords"]],
                         [InputPhase.CANCELLED, InputPhase.CANCELLED])
        self.assertTrue(bridge.state["outbox"][-1]["text"].endswith(
            "已丢弃切换前的运行结果和待处理消息。"))

    async def test_new_session_detaches_the_live_turn(self):
        bridge = self.bridge
        current = self.add_running_turn()
        bridge.backend.thread = AsyncMock(return_value="new-thread")
        bridge.backend.release_thread = AsyncMock(return_value=True)

        await handle(bridge, "/new", "")

        self.assertEqual(current["done"].result().status, "discarded")
        self.assertEqual(bridge.state["thread"], "new-thread")
        self.assertTrue(bridge.state["outbox"][-1]["text"].endswith(
            "已丢弃切换前的运行结果和待处理消息。"))

    async def test_model_change_updates_only_future_turns(self):
        bridge = self.bridge
        current = self.add_running_turn()
        bridge.state["currentInput"] = self.job("current")
        bridge.backend.info = {"model": "first", "effort": "low"}
        bridge.backend.models = AsyncMock(return_value=[
            {"model": "first"}, {"model": "second", "defaultReasoningEffort": "high"}])
        calls = []

        async def update(thread_id, **fields):
            calls.append((thread_id, fields))
            bridge.backend.info.update(fields)

        bridge.backend.update_settings = update

        await handle(bridge, "/model", "2")

        self.assertEqual(calls, [("current", {"model": "second", "effort": "high"})])
        self.assertIs(bridge.backend.active, current)
        self.assertFalse(current["done"].done())
        self.assertIn("currentInput", bridge.state)

    async def test_effort_and_cwd_update_only_future_turns(self):
        bridge = self.bridge
        current = self.add_running_turn()
        bridge.backend.info = {"model": "fixture", "effort": "low", "cwd": "/old"}
        bridge.backend.models = AsyncMock(return_value=[{
            "model": "fixture",
            "supportedReasoningEfforts": [{"reasoningEffort": "low"},
                                          {"reasoningEffort": "high"}],
        }])
        calls = []

        async def update(thread_id, **fields):
            calls.append((thread_id, fields))
            bridge.backend.info.update(fields)

        bridge.backend.update_settings = update

        await handle(bridge, "/effort", "high")
        await handle(bridge, "/cwd", self.temp.name)

        self.assertEqual(calls, [
            ("current", {"effort": "high"}),
            ("current", {"cwd": str(Path(self.temp.name).resolve())}),
        ])
        self.assertIs(bridge.backend.active, current)
        self.assertFalse(current["done"].done())

    async def test_setting_change_rejects_a_desktop_owned_turn(self):
        bridge = self.bridge
        self.add_running_turn(owned=False)
        bridge.backend.info = {"model": "fixture", "effort": "low"}
        bridge.backend.models = AsyncMock(return_value=[{
            "model": "fixture",
            "supportedReasoningEfforts": [{"reasoningEffort": "high"}],
        }])
        bridge.backend.update_settings = AsyncMock()

        with self.assertRaises(UserFacingError):
            await handle(bridge, "/effort", "high")
        bridge.backend.update_settings.assert_not_awaited()

    async def test_compact_requires_idle(self):
        bridge = self.bridge
        self.add_running_turn()
        bridge.backend.compact = AsyncMock()

        with self.assertRaises(UserFacingError):
            await handle(bridge, "/compact", "")
        bridge.backend.compact.assert_not_awaited()

    async def test_backend_change_requires_idle(self):
        bridge = self.bridge
        current = self.add_running_turn()
        bridge.backend_configs = {"codex": {}, "other": {}}
        bridge.backend_name = "codex"

        with self.assertRaises(UserFacingError):
            await handle(bridge, "/backend", "other")
        self.assertIs(bridge.backend.active, current)

    async def test_restart_requires_idle(self):
        bridge = self.bridge
        current = self.add_running_turn()

        await handle(bridge, "/restart", "")

        self.assertIs(bridge.backend.active, current)
        self.assertIn(bridge.prompt("BUSY_RESTART"), bridge.state["outbox"][-1]["text"])

    async def test_live_switch_predicate_allows_a_running_turn(self):
        bridge = self.bridge
        self.assertTrue(can_change_session(bridge))
        bridge.running = True
        self.assertFalse(can_change_session(bridge))
        self.add_running_turn()
        self.assertTrue(can_change_session(bridge))

    async def test_worker_drops_a_discarded_turn_without_replying(self):
        bridge = self.bridge
        bridge.media.incoming = AsyncMock(return_value=[])
        bridge.deliver_answer = AsyncMock()
        bridge.state["processingNotice"] = ""
        bridge.backend.run = AsyncMock(return_value=TurnResult(
            self.source(), "turn", "old answer", "/tmp", "discarded"))
        bridge.state["inbox"] = [self.job("first", InputPhase.PREPARING)]

        task = asyncio.create_task(bridge.worker())
        try:
            for _ in range(100):
                if (not bridge.state["inbox"] and "currentInput" not in bridge.state
                        and not bridge.running):
                    break
                await asyncio.sleep(0.01)
            self.assertEqual(bridge.state["outbox"], [])
            bridge.deliver_answer.assert_not_awaited()
            self.assertFalse(bridge.running)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_worker_does_not_requeue_a_discarded_deferred_turn(self):
        bridge = self.bridge
        bridge.media.incoming = AsyncMock(return_value=[])
        bridge.deliver_answer = AsyncMock()
        bridge.state["processingNotice"] = ""
        job = self.job("first", InputPhase.PREPARING)

        async def defer_after_discard(*_args, **_kwargs):
            job["discarded"] = "switch"
            raise DeferTurn("desktop turn changed")

        bridge.backend.run = defer_after_discard
        bridge.state["inbox"] = [job]

        task = asyncio.create_task(bridge.worker())
        try:
            for _ in range(100):
                if (not bridge.state["inbox"] and "currentInput" not in bridge.state
                        and not bridge.running):
                    break
                await asyncio.sleep(0.01)
            self.assertEqual(bridge.state["inbox"], [])
            self.assertEqual(bridge.state["outbox"], [])
            bridge.deliver_answer.assert_not_awaited()
            self.assertFalse(bridge.running)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
