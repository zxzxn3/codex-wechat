"""Detach target changes and future-turn setting updates."""
import asyncio
from dataclasses import asdict
from copy import deepcopy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from codex_wechat.app import Bridge
from codex_wechat.backend import (
    CodexBackend, DeferTurn, WRITER_FOLLOWED, WRITER_NATIVE, WRITER_RELEASED,
)
from codex_wechat.commands import _replace_target, handle
from codex_wechat.errors import UserFacingError
from codex_wechat.lifecycle import can_change_session, can_steer_input
from codex_wechat.records import InputPhase, Source, TurnResult
from codex_wechat.storage import Store
from codex_wechat.codex_transport import CodexRpcError


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

    async def test_update_settings_uses_the_app_follower_settings(self):
        backend = CodexBackend({}, "/tmp", lambda _: None)
        backend.selected_thread = "thread"
        backend.set_writer(WRITER_FOLLOWED, "thread")
        app = AsyncMock()
        app.thread_id = "thread"
        app.connected = True
        app.settings = AsyncMock()
        backend.app = app

        await backend.update_settings(
            "thread", model="model", effort="high", cwd="/workspace")

        app.settings.assert_awaited_once_with({
            "model": "model",
            "config": {"model_reasoning_effort": "high"},
            "cwd": "/workspace",
        })
        self.assertEqual(backend.info, {
            "model": "model", "effort": "high", "cwd": "/workspace"})

    async def test_prepare_thread_does_not_select_until_commit(self):
        backend = CodexBackend({}, "/tmp", lambda _: None)
        backend.selected_thread = "old"
        backend.set_writer(WRITER_NATIVE, "old")
        backend.rpc = AsyncMock(return_value={"thread": {"id": "target"}})

        prepared = await backend.prepare_thread("target")

        self.assertEqual(backend.selected_thread, "old")
        self.assertEqual(backend.writer_state("old"), WRITER_NATIVE)
        await backend.commit_prepared(prepared)
        self.assertEqual(backend.selected_thread, "target")
        self.assertEqual(backend.writer_state("target"), WRITER_NATIVE)

    async def test_prepare_thread_attaches_a_shared_active_target(self):
        backend = CodexBackend({}, "/tmp", lambda _: None)
        backend.selected_thread = "old"
        backend.blocked = True
        backend.rpc = AsyncMock(side_effect=CodexRpcError(
            -32600, "thread target already has an active writer", "thread/resume"))
        app = AsyncMock()
        app.thread_result = Mock(return_value={
            "thread": {"id": "target", "status": {"type": "active"}}})
        backend.attach_app = AsyncMock(return_value=app)

        prepared = await backend.prepare_thread("target")

        self.assertEqual(backend.selected_thread, "old")
        self.assertIs(prepared["app"], app)
        await backend.commit_prepared(prepared)
        self.assertEqual(backend.selected_thread, "target")
        self.assertEqual(backend.app, app)
        self.assertEqual(backend.writer_state("target"), WRITER_FOLLOWED)
        self.assertFalse(backend.blocked)

    async def test_shared_target_follower_stays_dormant_until_finish(self):
        backend = CodexBackend({}, "/tmp", lambda _: None)
        old = backend.new_turn("old", "turn", owned=True)
        backend.active = old
        backend.selected_thread = "old"
        backend.rpc = AsyncMock(side_effect=CodexRpcError(
            -32600, "thread target already has an active writer", "thread/resume"))
        app = AsyncMock()
        app.enabled = False
        app.activate = Mock()
        app.thread_result = Mock(return_value={
            "thread": {"id": "target", "status": {"type": "active"}}})
        backend.attach_app = AsyncMock(return_value=app)

        prepared = await backend.prepare_thread("target")
        await backend.commit_prepared(
            prepared, preserve_active=True, defer_cleanup=True)

        self.assertIs(backend.active, old)
        app.activate.assert_not_called()
        await backend.finish_prepared(prepared)
        app.activate.assert_called_once()

    async def test_finish_prepared_discovers_an_active_adopted_target(self):
        backend = self.bridge.backend
        backend.selected_thread = "target"
        backend.shared = True
        backend.active = None
        self.bridge.backend_started = True
        backend.transport.reader = Mock()
        backend.transport.reader.done.return_value = False
        backend.transport.proc = Mock(returncode=None)

        async def rpc(method, params):
            if method == "thread/read":
                return {"thread": {"id": "target", "status": {"type": "active"}}}
            if method == "thread/turns/list":
                return {"data": [{"id": "target-turn", "status": "inProgress"}]}
            return {}

        backend.rpc = rpc
        await backend.finish_prepared({"thread_id": "target", "adopt": True})

        self.assertEqual(backend.active["turn"], "target-turn")
        self.assertEqual(self.bridge.prompt_fields()["status"], "运行中")

    async def test_releasing_old_writer_is_not_blocked_by_target_follower(self):
        backend = CodexBackend({}, "/tmp", lambda _: None)
        backend.transport.reader = Mock()
        backend.transport.reader.done.return_value = False
        backend.transport.proc = Mock(returncode=None)
        backend.selected_thread = "target"
        backend.app = Mock(thread_id="target")
        backend.active = backend.new_turn("target", "turn", owned=True)
        backend.set_writer(WRITER_NATIVE, "old")
        backend.rpc = AsyncMock(return_value={})

        self.assertTrue(await backend.release_thread("old"))

        backend.rpc.assert_awaited_once_with(
            "thread/unsubscribe", {"threadId": "old"})
        self.assertEqual(backend.writer_state("old"), WRITER_RELEASED)

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
        bridge.backend.prepare_thread = AsyncMock(return_value={
            "thread_id": target,
            "result": {"thread": {"id": target}},
            "adopt": False,
            "desktop": False,
        })
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
        bridge.backend.prepare_thread = AsyncMock(return_value={
            "thread_id": "new-thread",
            "result": {"thread": {"id": "new-thread"}},
            "adopt": False,
            "desktop": False,
        })
        bridge.backend.release_thread = AsyncMock(return_value=True)

        await handle(bridge, "/new", "")

        self.assertEqual(current["done"].result().status, "discarded")
        self.assertEqual(bridge.state["thread"], "new-thread")
        self.assertTrue(bridge.state["outbox"][-1]["text"].endswith(
            "已丢弃切换前的运行结果和待处理消息。"))

    async def test_switch_target_validation_failure_keeps_the_old_target(self):
        bridge = self.bridge
        current = self.add_running_turn()
        bridge.state["currentInput"] = self.job("current")
        bridge.state["inbox"] = [self.job("queued", InputPhase.PREPARING)]
        bridge.state["outbox"] = [
            {"id": "old-output", "text": "old", "source": asdict(self.source())},
        ]
        bridge.backend.read_thread = AsyncMock(
            return_value={"status": {"type": "active"}})
        bridge.backend.shared = False

        with self.assertRaises(UserFacingError):
            await handle(bridge, "/switch", "00000000-0000-0000-0000-000000000001")

        self.assertIs(bridge.backend.active, current)
        self.assertFalse(current["done"].done())
        self.assertIn("currentInput", bridge.state)
        self.assertEqual(bridge.state["inbox"], [self.job("queued", InputPhase.PREPARING)])
        self.assertEqual([entry["id"] for entry in bridge.state["outbox"]], ["old-output"])
        self.assertEqual(bridge.state["thread"], "current")

    async def test_switch_system_error_target_keeps_the_old_target(self):
        bridge = self.bridge
        current = self.add_running_turn()
        bridge.backend.read_thread = AsyncMock(
            return_value={"status": {"type": "systemError"}})

        with self.assertRaises(UserFacingError):
            await handle(bridge, "/switch", "00000000-0000-0000-0000-000000000001")

        self.assertIs(bridge.backend.active, current)
        self.assertFalse(current["done"].done())
        self.assertEqual(bridge.state["thread"], "current")

    async def test_switch_prepare_failure_keeps_the_old_target(self):
        bridge = self.bridge
        current = self.add_running_turn()
        bridge.backend.read_thread = AsyncMock(
            return_value={"status": {"type": "idle"}})
        bridge.backend.prepare_thread = AsyncMock(side_effect=RuntimeError("resume failed"))

        with self.assertRaises(RuntimeError):
            await handle(bridge, "/switch", "00000000-0000-0000-0000-000000000001")

        self.assertIs(bridge.backend.active, current)
        self.assertFalse(current["done"].done())
        self.assertEqual(bridge.state["thread"], "current")

    async def test_commit_failure_keeps_the_old_target(self):
        bridge = self.bridge
        current = self.add_running_turn()
        bridge.state["currentInput"] = self.job("current")
        bridge.state["inbox"] = [self.job("queued", InputPhase.PREPARING)]
        bridge.state["outbox"] = [
            {"id": "old-output", "text": "old", "source": asdict(self.source())}]
        bridge.backend.read_thread = AsyncMock(
            return_value={"status": {"type": "idle"}})
        bridge.backend.prepare_thread = AsyncMock(return_value={
            "thread_id": "target",
            "result": {"thread": {"id": "target"}},
            "adopt": False,
            "desktop": False,
        })
        bridge.backend.commit_prepared = AsyncMock(
            side_effect=RuntimeError("commit failed"))

        with self.assertRaises(RuntimeError):
            await handle(bridge, "/switch", "00000000-0000-0000-0000-000000000001")

        self.assertIs(bridge.backend.active, current)
        self.assertFalse(current["done"].done())
        self.assertEqual(bridge.state["thread"], "current")
        self.assertIn("currentInput", bridge.state)
        self.assertEqual(bridge.state["inbox"], [self.job("queued", InputPhase.PREPARING)])
        self.assertEqual([entry["id"] for entry in bridge.state["outbox"]], ["old-output"])

    async def test_save_failure_keeps_the_old_target(self):
        bridge = self.bridge
        current = self.add_running_turn()
        original_current = self.job("current")
        original_queued = self.job("queued", InputPhase.PREPARING)
        bridge.state["currentInput"] = original_current
        bridge.state["inbox"] = [original_queued]
        bridge.state["outbox"] = [
            {"id": "old-output", "text": "old", "source": asdict(self.source())}]
        bridge.backend.read_thread = AsyncMock(
            return_value={"status": {"type": "idle"}})
        bridge.backend.prepare_thread = AsyncMock(return_value={
            "thread_id": "target",
            "result": {"thread": {"id": "target"}},
            "adopt": False,
            "desktop": False,
        })
        bridge.backend.release_thread = AsyncMock(return_value=True)
        bridge.save = Mock(side_effect=OSError("disk full"))

        with self.assertRaises(OSError):
            await handle(bridge, "/switch", "00000000-0000-0000-0000-000000000001")

        self.assertIs(bridge.backend.active, current)
        self.assertFalse(current["done"].done())
        self.assertEqual(bridge.state["thread"], "current")
        self.assertIs(bridge.state["currentInput"], original_current)
        self.assertIs(bridge.state["inbox"][0], original_queued)
        bridge.backend.release_thread.assert_awaited_once_with("target")

    async def test_new_save_failure_deletes_the_created_thread(self):
        bridge = self.bridge
        self.add_running_turn()
        calls = []

        async def rpc(method, params):
            calls.append((method, params))
            if method == "thread/start":
                return {"thread": {"id": "new-thread"}}
            return {}

        bridge.backend.rpc = rpc
        bridge.save = Mock(side_effect=OSError("disk full"))

        with self.assertRaises(OSError):
            await handle(bridge, "/new", "")

        self.assertIn(("thread/delete", {"threadId": "new-thread"}), calls)
        self.assertNotIn("thread/unsubscribe", [method for method, _ in calls])
        self.assertEqual(bridge.state["thread"], "current")

    async def test_switch_save_failure_unsubscribes_without_deleting(self):
        bridge = self.bridge
        self.add_running_turn()
        calls = []

        async def rpc(method, params):
            calls.append((method, params))
            if method == "thread/read":
                return {"thread": {"id": "target", "status": {"type": "idle"}}}
            if method == "thread/resume":
                return {"thread": {"id": "target"}}
            return {}

        bridge.backend.rpc = rpc
        bridge.backend.transport.reader = Mock()
        bridge.backend.transport.reader.done.return_value = False
        bridge.backend.transport.proc = Mock(returncode=None)
        bridge.save = Mock(side_effect=OSError("disk full"))

        with self.assertRaises(OSError):
            await handle(bridge, "/switch", "00000000-0000-0000-0000-000000000001")

        self.assertIn(("thread/unsubscribe", {"threadId": "target"}), calls)
        self.assertNotIn("thread/delete", [method for method, _ in calls])
        self.assertEqual(bridge.state["thread"], "current")

    async def test_new_target_prepare_failure_keeps_the_old_target(self):
        bridge = self.bridge
        current = self.add_running_turn()
        bridge.state["currentInput"] = self.job("current")
        bridge.state["inbox"] = [self.job("queued", InputPhase.PREPARING)]
        bridge.state["outbox"] = [
            {"id": "old-output", "text": "old", "source": asdict(self.source())},
        ]
        bridge.backend.prepare_thread = AsyncMock(side_effect=RuntimeError("start failed"))

        with self.assertRaises(RuntimeError):
            await handle(bridge, "/new", "")

        self.assertIs(bridge.backend.active, current)
        self.assertFalse(current["done"].done())
        self.assertIn("currentInput", bridge.state)
        self.assertEqual(bridge.state["inbox"], [self.job("queued", InputPhase.PREPARING)])
        self.assertEqual([entry["id"] for entry in bridge.state["outbox"]], ["old-output"])
        self.assertEqual(bridge.state["thread"], "current")

    async def test_foreign_request_does_not_notify_the_new_selection(self):
        notices = []
        backend = CodexBackend({}, "/tmp", notices.append)
        backend.selected_thread = "new"
        backend.shared = False
        app = AsyncMock()
        app.thread_id = "new"
        app.connected = True
        backend.app = app
        backend.transport.write = AsyncMock()

        await backend.server_request({
            "id": 7,
            "method": "item/tool/requestUserInput",
            "params": {"threadId": "old"},
        })

        self.assertEqual(notices, [])
        backend.transport.write.assert_awaited_once_with({
            "id": 7,
            "error": {
                "code": -32601,
                "message": "This bridge does not support this request",
            },
        })
        app.respond.assert_not_awaited()

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

    async def test_steering_waits_for_the_session_switch_lock(self):
        bridge = self.bridge
        bridge.running = True
        bridge.state["inbox"] = [self.job("steer")]
        self.assertTrue(can_steer_input(bridge))
        async with bridge.session_lock:
            self.assertFalse(can_steer_input(bridge))

    async def test_steering_accepted_after_discard_is_not_acknowledged(self):
        bridge = self.bridge
        bridge.media.incoming = AsyncMock(return_value=[])
        bridge.state["processingNotice"] = ""
        job = self.job("steer")
        bridge.state["inbox"] = [job]
        bridge.backend.active = bridge.backend.new_turn("current", "turn", owned=True)
        bridge.typing.start = AsyncMock(return_value=True)
        bridge.typing.stop = AsyncMock()
        bridge.reply = Mock()

        async def steer(*_args, **_kwargs):
            job["discarded"] = "switch"
            return True

        bridge.backend.steer = steer
        task = asyncio.create_task(bridge.steering_worker())
        try:
            bridge.steer_wake.set()
            for _ in range(100):
                if job.get("discarded"):
                    break
                await asyncio.sleep(0.01)
            self.assertEqual(job.get("discarded"), "switch")
            bridge.reply.assert_not_called()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_old_inflight_failure_does_not_notify_the_new_session(self):
        bridge = self.bridge
        entry = {"id": "old", "text": "old", "context": "context",
                 "source": asdict(self.source("old"))}
        bridge.state["outbox"] = [entry]
        bridge.fail_output(entry)
        self.assertEqual(bridge.state["outbox"], [])
        self.assertEqual(len(bridge.state["failedOutbox"]), 1)

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

    async def test_worker_requires_current_source_before_delivery(self):
        bridge = self.bridge
        bridge.media.incoming = AsyncMock(return_value=[])
        bridge.deliver_answer = AsyncMock()
        bridge.state["processingNotice"] = ""
        answer = TurnResult(self.source("old"), "turn", "answer", "/tmp", "completed")
        bridge.backend.run = AsyncMock(return_value=answer)
        bridge.state["inbox"] = [self.job("first", InputPhase.PREPARING)]

        task = asyncio.create_task(bridge.worker())
        try:
            for _ in range(100):
                if (not bridge.state["inbox"] and "currentInput" not in bridge.state
                        and not bridge.running):
                    break
                await asyncio.sleep(0.01)
            bridge.deliver_answer.assert_awaited_once()
            self.assertTrue(bridge.deliver_answer.await_args.kwargs["require_current"])
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_switch_marks_the_live_job_objects_not_only_the_staged_copy(self):
        # The discard is staged into a deepcopy so a failed save leaves memory
        # untouched, but the worker keeps the original job objects: they must
        # carry the same flag or its requeue and failure paths revive them.
        bridge = self.bridge
        current = self.add_running_turn()
        live = self.job("live")
        queued = self.job("queued", InputPhase.PREPARING)
        bridge.state["currentInput"] = live
        bridge.state["inbox"] = [queued]
        bridge.backend.read_thread = AsyncMock(
            return_value={"status": {"type": "idle"}})
        target = "00000000-0000-0000-0000-000000000001"
        bridge.backend.prepare_thread = AsyncMock(return_value={
            "thread_id": target,
            "result": {"thread": {"id": target}},
            "adopt": False,
        })
        bridge.backend.release_thread = AsyncMock(return_value=True)

        await handle(bridge, "/switch", target)

        self.assertEqual(current["done"].result().status, "discarded")
        self.assertEqual(live.get("discarded"), "switch")
        self.assertEqual(queued.get("discarded"), "switch")

    async def test_switch_discard_reaches_live_worker_and_drain_event(self):
        bridge = self.bridge
        bridge.media.incoming = AsyncMock(return_value=[])
        bridge.deliver_answer = AsyncMock()
        bridge.state["processingNotice"] = ""
        live = self.job("live", InputPhase.PREPARING)
        bridge.state["inbox"] = [live]
        bridge.state["outbox"] = [
            {"id": "old-output", "text": "old", "source": asdict(self.source())}]
        bridge.outbox_drained.clear()
        run_started = asyncio.Event()
        finish_run = asyncio.Event()
        target = "00000000-0000-0000-0000-000000000001"

        async def run(*_args, **_kwargs):
            bridge.backend.active = bridge.backend.new_turn(
                "current", "turn", owned=True)
            run_started.set()
            await finish_run.wait()
            raise DeferTurn("desktop turn changed")

        bridge.backend.run = run
        bridge.backend.release_thread = AsyncMock(return_value=True)
        prepared = {
            "thread_id": target,
            "result": {"thread": {"id": target}},
            "adopt": False,
        }

        task = asyncio.create_task(bridge.worker())
        try:
            await asyncio.wait_for(run_started.wait(), 1)
            await _replace_target(bridge, prepared)
            finish_run.set()
            for _ in range(100):
                if not bridge.running and not bridge.state["inbox"]:
                    break
                await asyncio.sleep(0.01)

            self.assertEqual(bridge.state["outbox"], [])
            self.assertTrue(bridge.outbox_drained.is_set())
            self.assertEqual(bridge.state["inbox"], [])
            self.assertNotIn("currentInput", bridge.state)
            self.assertEqual(live.get("discarded"), "switch")
            self.assertFalse(bridge.running)
            bridge.deliver_answer.assert_not_awaited()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_staged_discard_realigns_the_drained_event(self):
        # A staged discard cannot update the event itself, so the switch must
        # re-align it once the copied state is live; otherwise an empty outbox
        # keeps reporting "not drained" to the restart grace wait.
        bridge = self.bridge
        bridge.state["outbox"] = [
            {"id": "old", "text": "old", "source": asdict(self.source())}]
        bridge.outbox_drained.clear()
        staged = deepcopy(bridge.state)

        bridge.output.discard_source(
            bridge.backend_name, "current", state=staged, persist=False)
        bridge.state.clear()
        bridge.state.update(staged)
        bridge.output.sync_drained()

        self.assertEqual(bridge.state["outbox"], [])
        self.assertTrue(bridge.outbox_drained.is_set())

    async def test_switch_clears_stale_session_and_model_choices(self):
        bridge = self.bridge
        bridge.state["sessionChoices"] = ["old-session"]
        bridge.model_choices = [{"model": "old-model"}]
        target = "00000000-0000-0000-0000-000000000001"
        bridge.backend.release_thread = AsyncMock(return_value=True)

        await _replace_target(bridge, {
            "thread_id": target,
            "result": {"thread": {"id": target}},
            "adopt": False,
        })

        self.assertNotIn("sessionChoices", bridge.state)
        self.assertEqual(bridge.model_choices, [])

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

    async def test_preparation_failure_does_not_restore_a_discarded_job(self):
        bridge = self.bridge
        bridge.media.incoming = AsyncMock(return_value=[])
        bridge.state["processingNotice"] = ""
        job = self.job("old", InputPhase.PREPARING)
        job["discarded"] = "switch"
        bridge.state["inbox"] = [job]

        async def fail(_bridge):
            raise RuntimeError("prepare failed")

        with patch("codex_wechat.input_delivery.ensure_session", fail):
            task = asyncio.create_task(bridge.worker())
            try:
                for _ in range(100):
                    if (not bridge.running and not bridge.state["inbox"]
                            and "currentInput" not in bridge.state):
                        break
                    await asyncio.sleep(0.01)
                self.assertEqual(bridge.state["inbox"], [])
                self.assertNotIn("currentInput", bridge.state)
                self.assertFalse(bridge.input_paused)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
