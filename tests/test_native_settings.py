"""Native setting authority without connecting to Codex."""
import asyncio
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from codex_wechat.app import Bridge
from codex_wechat.backend import CodexBackend, WRITER_BUSY, WRITER_NATIVE, WRITER_RELEASED
from codex_wechat.codex_transport import CodexRpcError
from codex_wechat.commands import activate
from codex_wechat.input_delivery import InputDelivery
from codex_wechat.backend import render_prompt
from codex_wechat.prompts import prompt_text
from codex_wechat.storage import Store


class NativeSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def test_switching_keeps_writer_state_per_thread(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root) / "state")
            try:
                bridge = Bridge(store, {"user": "owner"}, {})
                bridge.state["thread"] = "old"
                bridge.backend.selected_thread = "old"
                bridge.backend.set_writer(WRITER_NATIVE, "old")
                bridge.backend.read_thread = AsyncMock(return_value={"status": {"type": "idle"}})

                async def select(thread_id, overrides=None):
                    bridge.backend.set_writer(WRITER_NATIVE, thread_id)
                    return thread_id

                async def release(thread_id, follow=False):
                    bridge.backend.set_writer(WRITER_RELEASED, thread_id)
                    return True

                bridge.backend.thread = AsyncMock(side_effect=select)
                bridge.backend.release_thread = AsyncMock(side_effect=release)
                await activate(bridge, "new")
                self.assertEqual(bridge.backend.writer_state("old"), WRITER_RELEASED)
                self.assertEqual(bridge.backend.writer_state("new"), WRITER_RELEASED)
            finally:
                store.lock.close()

    async def test_activation_returns_the_writer_so_the_app_can_open_the_thread(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root) / "state")
            try:
                bridge = Bridge(store, {"user": "owner"}, {})
                bridge.backend.read_thread = AsyncMock(return_value={"status": {"type": "idle"}})
                bridge.backend.thread = AsyncMock(
                    side_effect=lambda thread_id=None, overrides=None: thread_id or "created")
                bridge.backend.release_thread = AsyncMock(return_value=True)
                await activate(bridge, "target")
                bridge.backend.release_thread.assert_awaited_once_with("target", follow=True)
                self.assertEqual(bridge.state["thread"], "target")
                bridge.backend.release_thread.reset_mock()
                await activate(bridge, "other")
                self.assertEqual([call.args for call in bridge.backend.release_thread.await_args_list],
                                 [("target",), ("other",)])
            finally:
                store.lock.close()

    async def test_desktop_writer_conflict_keeps_the_bridge_loaded(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root) / "state")
            try:
                bridge = Bridge(store, {"user": "owner"}, {})
                conflict = CodexRpcError(-32600, "thread t already has an active writer")
                bridge.backend.read_thread = AsyncMock(return_value={"status": {"type": "idle"},
                                                                     "name": "name", "cwd": "/cwd"})
                bridge.backend.thread = AsyncMock(side_effect=conflict)
                bridge.backend.release_thread = AsyncMock(return_value=True)
                await activate(bridge, "t")
                self.assertTrue(bridge.loaded)
                self.assertEqual(bridge.state["thread"], "t")
                self.assertEqual(bridge.backend.selected_thread, "t")
                self.assertEqual(bridge.backend.writer_state("t"), WRITER_BUSY)
                self.assertEqual(bridge.backend.info["cwd"], "/cwd")
                bridge.backend.release_thread.assert_not_awaited()
            finally:
                store.lock.close()

    async def test_writer_conflict_queues_the_message_once(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root) / "state")
            try:
                bridge = Bridge(store, {"user": "owner"}, {})
                delivery = InputDelivery(bridge)
                bridge.reply = Mock()
                job = {"text": "hi", "requestId": "req-1", "phase": "preparing"}
                self.assertTrue(delivery.queue_writer_conflict(bridge, job))
                self.assertEqual(bridge.state["inbox"], [job])
                self.assertEqual(bridge.reply.call_count, 1)
                self.assertIn("已排队", bridge.reply.call_args.args[0])
                self.assertGreater(delivery.writer_wait_left(job), 0)
                self.assertTrue(delivery.queue_writer_conflict(bridge, job))
                self.assertEqual(bridge.reply.call_count, 1)
                job["writerQueuedAt"] = 0
                self.assertFalse(delivery.queue_writer_conflict(bridge, job))
            finally:
                store.lock.close()

    async def test_failed_resume_keeps_watching_for_the_desktop(self):
        backend = CodexBackend({}, "/tmp", lambda _: None)
        backend.transport.proc = SimpleNamespace(returncode=None)
        backend.transport.reader = SimpleNamespace(done=lambda: False)
        backend.set_writer(WRITER_RELEASED, "t")
        backend.input_items = AsyncMock(return_value=[])
        backend.thread = AsyncMock(side_effect=CodexRpcError(
            -32600, "thread t already has an active writer"))
        with self.assertRaises(CodexRpcError):
            await backend.run("t", "hello")
        self.assertEqual(backend.writer_state("t"), WRITER_RELEASED)

    async def test_settings_event_updates_only_selected_thread(self):
        backend = CodexBackend({}, "/tmp", lambda _: None)
        backend.selected_thread = "t"
        backend.event({"method": "thread/settings/updated", "params": {"threadId": "other",
                       "threadSettings": {"model": "other"}}})
        self.assertEqual(backend.info, {})
        backend.event({"method": "thread/settings/updated", "params": {"threadId": "t",
                       "threadSettings": {"model": "desktop", "effort": None, "cwd": "/tmp"}}})
        self.assertEqual(backend.info, {"model": "desktop", "effort": None, "cwd": "/tmp"})

    async def test_new_turn_does_not_reapply_cached_settings(self):
        backend = CodexBackend({"sharedDaemon": False}, "/tmp", lambda _: None)
        backend.selected_thread = "t"
        backend.info = {"model": "old", "effort": "high", "cwd": "/old"}
        backend.input_items = AsyncMock(return_value=[])
        async def rpc(method, params):
            if method == "turn/start":
                self.assertEqual(params, {"threadId": "t", "input": [], "clientUserMessageId": "client"})
                backend.event({"method": "turn/completed", "params": {"threadId": "t",
                              "turn": {"id": "turn", "status": "completed"}}})
                return {"turn": {"id": "turn"}}
            return {}
        backend.rpc = rpc
        await asyncio.wait_for(backend.run("t", "input", client_id="client"), 1)

    async def test_failed_turn_reports_an_exhausted_usage_limit(self):
        backend = CodexBackend({}, "/tmp", lambda _: None)
        backend.selected_thread = "t"
        backend.active = backend.new_turn("t", "turn", owned=True)
        backend.event({"method": "turn/completed", "params": {
            "threadId": "t", "turn": {
                "id": "turn", "status": "failed",
                "error": {"message": "quota", "codexErrorInfo": "usageLimitExceeded"},
            }}})
        result = await backend.active["done"]
        self.assertEqual(result.text, prompt_text("TURN_FAILED_USAGE_LIMIT"))

    async def test_usage_limit_error_notification_reaches_completion(self):
        backend = CodexBackend({}, "/tmp", lambda _: None)
        backend.selected_thread = "t"
        backend.active = backend.new_turn("t", "turn", owned=True)
        backend.event({"method": "error", "params": {
            "threadId": "t", "turnId": "turn", "willRetry": False,
            "error": {"message": "quota", "codexErrorInfo": "usageLimitExceeded"},
        }})
        backend.event({"method": "turn/completed", "params": {
            "threadId": "t", "turn": {"id": "turn", "status": "failed"},
        }})
        result = await backend.active["done"]
        self.assertEqual(result.text, prompt_text("TURN_FAILED_USAGE_LIMIT"))

    async def test_other_failed_turn_stays_generic(self):
        backend = CodexBackend({}, "/tmp", lambda _: None)
        backend.selected_thread = "t"
        backend.active = backend.new_turn("t", "turn", owned=True)
        backend.event({"method": "turn/completed", "params": {
            "threadId": "t", "turn": {
                "id": "turn", "status": "failed",
                "error": {"message": "other", "codexErrorInfo": "other"},
            }}})
        result = await backend.active["done"]
        self.assertEqual(result.text, prompt_text("TURN_FAILED"))

    async def test_known_turn_error_codes_map_to_specific_prompts(self):
        cases = {
            "sessionBudgetExceeded": "TURN_FAILED_SESSION_BUDGET",
            "contextWindowExceeded": "TURN_FAILED_CONTEXT",
            "rateLimitExceeded": "TURN_FAILED_RATE_LIMIT",
            "unauthorized": "TURN_FAILED_UNAUTHORIZED",
            "modelNotFound": "TURN_FAILED_MODEL_NOT_FOUND",
            "flexUnavailable": "TURN_FAILED_SERVICE_UNAVAILABLE",
            "serverOverloaded": "TURN_FAILED_SERVICE_UNAVAILABLE",
            "policyCapacityUnavailable": "TURN_FAILED_SERVICE_UNAVAILABLE",
            "cyberPolicy": "TURN_FAILED_POLICY",
            "misalignmentPolicyViolation": "TURN_FAILED_POLICY",
            "tooManyDenials": "TURN_FAILED_TOO_MANY_DENIALS",
            "sandboxError": "TURN_FAILED_SANDBOX",
        }
        for code, prompt_name in cases.items():
            with self.subTest(code=code):
                backend = CodexBackend({}, "/tmp", lambda _: None)
                backend.selected_thread = "t"
                backend.active = backend.new_turn("t", "turn", owned=True)
                backend.event({"method": "turn/completed", "params": {
                    "threadId": "t", "turn": {
                        "id": "turn", "status": "failed",
                        "error": {"message": "failure", "codexErrorInfo": code},
                    }}})
                result = await backend.active["done"]
                self.assertEqual(result.text, render_prompt(prompt_name))

    async def test_http_error_objects_share_the_same_status_mapping(self):
        cases = (
            ("httpConnectionFailed", "httpStatusCode", 402, "TURN_FAILED_USAGE_LIMIT"),
            ("responseStreamConnectionFailed", "httpStatusCode", 429, "TURN_FAILED_RATE_LIMIT"),
            ("responseStreamDisconnected", "httpStatusCode", 503, "TURN_FAILED_SERVICE_UNAVAILABLE"),
            ("responseTooManyFailedAttempts", "httpStatusCode", None,
             "TURN_FAILED_SERVICE_UNAVAILABLE"),
        )
        for name, status_key, status, prompt_name in cases:
            with self.subTest(name=name, status=status):
                detail = {} if status is None else {status_key: status}
                error = {
                    "message": "upstream failure",
                    "codexErrorInfo": {name: detail},
                }
                backend = CodexBackend({}, "/tmp", lambda _: None)
                backend.selected_thread = "t"
                backend.active = backend.new_turn("t", "turn", owned=True)
                backend.event({"method": "turn/completed", "params": {
                    "threadId": "t", "turn": {
                        "id": "turn", "status": "failed", "error": error,
                    }}})
                result = await backend.active["done"]
                self.assertEqual(result.text, render_prompt(prompt_name))

    async def test_insufficient_balance_message_still_uses_the_usage_limit_prompt(self):
        backend = CodexBackend({}, "/tmp", lambda _: None)
        backend.selected_thread = "t"
        backend.active = backend.new_turn("t", "turn", owned=True)
        backend.event({"method": "turn/completed", "params": {
            "threadId": "t", "turn": {
                "id": "turn", "status": "failed",
                "error": {"message": "Insufficient Balance"},
            }}})
        result = await backend.active["done"]
        self.assertEqual(result.text, prompt_text("TURN_FAILED_USAGE_LIMIT"))
