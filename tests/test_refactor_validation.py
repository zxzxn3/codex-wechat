import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from codex_wechat.app import Bridge
from codex_wechat.backend import CodexBackend
from codex_wechat.command_specs import COMMANDS, HELP_ALL, REGISTRY, command_lane
from codex_wechat.commands import HANDLERS, handle, parse_control, validate
from codex_wechat.configuration import (ConfigLoader, DEFAULT_COMPLETION_TEMPLATE,
                           completion_template, processing_template)
from codex_wechat.errors import UserFacingError
from codex_wechat.storage import Store
from codex_wechat.usage import UsageCache
from codex_wechat.codex_transport import CodexRpcError, CodexTransport


class ConfigLoaderExtraTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch("codex_wechat.configuration.log_event"))
        self.ticks = 0

    def rewrite(self, path, payload):
        path.write_text(json.dumps(payload))
        # Change detection is (inode, mtime, size); Windows clock ticks can
        # repeat, so equal-length rewrites need an explicit distinct stamp.
        self.ticks += 1
        stamp = 1_700_000_000_000_000_000 + self.ticks * 1_000_000
        os.utime(path, ns=(stamp, stamp))

    def test_group_validation_retention_deletion_and_override_scope(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "config.json"
            initial = {
                "processingNotice": "file ack",
                "completionNotice": "file done",
                "sourceHint": "hint-a",
                "slicing": {"smart": True, "targetChars": 600},
                "compactionNotice": False,
                "quotaWarningThresholds": {"primary": [50], "secondary": [25]},
            }
            self.rewrite(path, initial)
            config = dict(initial)
            state = {"processingNotice": "chat ack", "completionNotice": "chat done"}
            loader = ConfigLoader(config, path)

            unrelated = dict(initial, sourceHint="hint-b",
                             slicing={"smart": False, "targetChars": 800})
            self.rewrite(path, unrelated)
            self.assertFalse(loader.reload(state))
            self.assertEqual(config["sourceHint"], "hint-b")
            self.assertEqual(loader.hot.slicing.target_chars, 800)
            self.assertEqual(state["processingNotice"], "chat ack")

            invalid = dict(unrelated, sourceHint="must-not-apply",
                           slicing={"smart": False, "targetChars": 1},
                           compactionNotice=True)
            self.rewrite(path, invalid)
            self.assertFalse(loader.reload(state))
            self.assertEqual(config["sourceHint"], "hint-b")
            self.assertEqual(loader.hot.slicing.target_chars, 800)
            self.assertFalse(loader.hot.notifications.compaction)
            self.assertEqual(state["completionNotice"], "chat done")

            deleted = dict(unrelated, sourceHint="hint-c",
                           slicing={"smart": False, "targetChars": 900})
            deleted.pop("processingNotice")
            deleted.pop("completionNotice")
            self.rewrite(path, deleted)
            self.assertTrue(loader.reload(state))
            self.assertNotIn("processingNotice", config)
            self.assertNotIn("completionNotice", config)
            self.assertNotIn("processingNotice", state)
            self.assertNotIn("completionNotice", state)
            self.assertEqual(processing_template(config, state),
                             "`IN < ~{turn_total_tokens} ({ingress_ms}ms)`")
            self.assertEqual(completion_template(config, state), DEFAULT_COMPLETION_TEMPLATE)

            state["processingNotice"] = "new chat override"
            unrelated_hot = dict(deleted, quotaWarningThresholds={
                "primary": [25], "secondary": [10]})
            self.rewrite(path, unrelated_hot)
            self.assertFalse(loader.reload(state))
            self.assertEqual(state["processingNotice"], "new chat override")
            self.assertEqual(loader.hot.notifications.quota_thresholds,
                             {"primary": (25,), "secondary": (10,)})


class CommandMetadataExtraTests(unittest.TestCase):
    def test_catalog_registry_lanes_and_handlers_are_consistent(self):
        self.assertEqual(set(HANDLERS), {
            spec.name for spec in COMMANDS if spec.route != "input"})
        for spec in COMMANDS:
            self.assertIn(spec.help_usage or spec.usage, HELP_ALL)
            for alias in spec.aliases:
                self.assertIs(REGISTRY[alias], spec)
            if spec.route != "input":
                self.assertEqual(HANDLERS[spec.name].__name__,
                                 "command_" + (spec.handler or spec.name.removeprefix("/")))
        self.assertNotIn("/models", REGISTRY)
        self.assertEqual(command_lane("/backend", ""), "local")
        self.assertEqual(command_lane("/backend", "codex"), "backend")
        self.assertEqual(command_lane("/cwd", ""), "local")
        self.assertEqual(command_lane("/cwd", "/tmp"), "backend")
        self.assertEqual(command_lane("/restart", ""), "local")
        self.assertEqual(command_lane("/stop", ""), "urgent")
        self.assertEqual(command_lane("/get", "file"), "media")
        self.assertEqual(command_lane("/ping", ""), "probe")
        self.assertEqual(parse_control("  /switch \t 2  "), ("/switch", "2"))
        with self.assertRaises(UserFacingError):
            validate(REGISTRY["/status"], "unexpected")
        with self.assertRaises(UserFacingError):
            validate(REGISTRY["/switch"], "")


class TransportExtraTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_rpc_removes_pending_and_errors_are_redacted(self):
        transport = CodexTransport(
            {}, "/tmp", on_request=AsyncMock(), on_event=AsyncMock(),
            on_connect=AsyncMock(), on_disconnect=AsyncMock())
        transport.write = AsyncMock()
        task = asyncio.create_task(transport.rpc("thread/read", {}))
        await asyncio.sleep(0)
        self.assertEqual(len(transport.requests), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(transport.requests, {})

        error = CodexRpcError(
            -32603, "failed https://example.invalid/a sk-secret token=hidden")
        self.assertNotIn("example.invalid", str(error))
        self.assertNotIn("sk-secret", str(error))
        self.assertNotIn("hidden", str(error))


class UsageCacheExtraTests(unittest.TestCase):
    def test_cache_is_bounded_and_tracks_replacements(self):
        with tempfile.TemporaryDirectory() as root:
            paths = []
            for index in range(25):
                path = Path(root) / f"thread-{index}.jsonl"
                path.write_text(f"revision-{index}")
                paths.append(path)
            cache = UsageCache()
            with patch("codex_wechat.usage.stored_usage", side_effect=lambda *args: {"file": args[0].name}):
                for path in paths:
                    cache.read(path, path.stem)
                self.assertLessEqual(len(cache.entries), 20)
                target = paths[0]
                before = len(cache.entries)
                cache.read(target, target.stem)
                self.assertGreaterEqual(len(cache.entries), before)
                replacement = Path(root) / "replacement"
                replacement.write_text("replacement")
                replacement.replace(target)
                cache.read(target, target.stem)
            cache.clear()
            self.assertEqual(cache.entries, {})


class MediaSnapshotExtraTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_control_freezes_origin_at_receive_time(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root) / "state")
            try:
                bridge = Bridge(store, {"user": "owner"}, {})
                bridge.state["context"] = "context-old"
                bridge.state["thread"] = "thread-old"
                bridge.backend.info["cwd"] = "/origin"
                bridge.prepare_asset = AsyncMock(return_value={
                    "path": "/origin/snapshot.txt", "name": "file.txt", "kind": "file"})
                bridge.queue_asset = Mock()
                await bridge.receive({"msgs": [{
                    "message_id": "send",
                    "from_user_id": "owner",
                    "message_type": 1,
                    "context_token": "context-old",
                    "item_list": [{"type": 1, "text_item": {
                        "text": "/getfile /origin/file.txt"}}],
                }]})
                text, received = bridge.controls["media"].get_nowait()
                bridge.state["context"] = "context-new"
                bridge.state["thread"] = "thread-new"
                bridge.backend.info["cwd"] = "/changed"
                await handle(bridge, *parse_control(text), received=received)
                origin = bridge.prepare_asset.await_args.kwargs["origin"]
                self.assertEqual(origin["context"], "context-old")
                self.assertEqual(origin["cwd"], "/origin")
                self.assertEqual(origin["source"].thread, "thread-old")
                self.assertEqual(bridge.prepare_asset.await_args.args,
                                 ("/origin/file.txt", "file"))
                self.assertIs(bridge.queue_asset.call_args.kwargs["origin"], origin)
            finally:
                store.lock.close()

    async def test_restart_rejects_running_and_session_lock_without_backend(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root) / "state")
            try:
                bridge = Bridge(store, {"user": "owner"}, {})
                bridge.state["context"] = "context"
                bridge.running = True
                await bridge.control("/restart")
                self.assertIn("有尚未完成的任务",
                              bridge.state["outbox"][-1]["text"])
                self.assertFalse(bridge.backend_started)

                bridge.running = False
                async with bridge.session_lock:
                    await bridge.control("/restart")
                self.assertIn("有尚未完成的任务",
                              bridge.state["outbox"][-1]["text"])
                self.assertFalse(bridge.backend_started)
            finally:
                store.lock.close()


class NativeSettingsExtraTests(unittest.IsolatedAsyncioTestCase):
    async def test_stale_runtime_read_cannot_overwrite_settings_event(self):
        backend = CodexBackend({"sharedDaemon": True}, "/tmp", lambda _: None)
        backend.selected_thread = "thread"
        backend.info = {"model": "old"}
        entered = asyncio.Event()
        release = asyncio.Event()

        async def stale_read(_thread):
            entered.set()
            await release.wait()
            return {"model": "stale", "status": {"type": "idle"}}

        backend.read_thread = stale_read
        refresh = asyncio.create_task(backend.refresh_runtime())
        await entered.wait()
        backend.event({"method": "thread/settings/updated", "params": {
            "threadId": "thread", "threadSettings": {"model": "new"}}})
        release.set()
        await refresh
        self.assertEqual(backend.info["model"], "new")
        self.assertEqual(backend.runtime_generation, 1)

    async def test_steer_keeps_expected_turn_and_early_events_replay(self):
        backend = CodexBackend({"sharedDaemon": False}, "/tmp", lambda _: None)
        backend.selected_thread = "thread"
        backend.input_items = AsyncMock(return_value=[])
        backend.active = backend.new_turn("thread", "turn", owned=False)
        phases = []

        async def steer_rpc(method, params):
            self.assertEqual(method, "turn/steer")
            self.assertEqual(params["expectedTurnId"], "turn")
            self.assertEqual(params["clientUserMessageId"], "client")
            return {}

        backend.rpc = steer_rpc
        def dispatch(phase, turn):
            phases.append((phase, turn))
            return True

        accepted = await backend.steer(
            "thread", "turn", "correction", client_id="client",
            dispatch=dispatch)
        self.assertTrue(accepted)
        self.assertEqual(phases, [("dispatching", "turn"), ("accepted", "turn")])

        backend.active = None
        backend.input_items = AsyncMock(return_value=[])

        async def start_rpc(method, params):
            self.assertEqual(method, "turn/start")
            self.assertNotIn("model", params)
            self.assertNotIn("effort", params)
            self.assertNotIn("cwd", params)
            self.assertEqual(params["clientUserMessageId"], "client")
            backend.event({"method": "turn/started", "params": {
                "threadId": "thread", "turn": {"id": "next"}}})
            backend.event({"method": "item/completed", "params": {
                "threadId": "thread", "turnId": "next", "item": {
                    "id": "answer", "type": "agentMessage",
                    "phase": "final_answer", "text": "early result"}}})
            backend.event({"method": "turn/completed", "params": {
                "threadId": "thread",
                "turn": {"id": "next", "status": "completed"}}})
            return {"turn": {"id": "next"}}

        backend.rpc = start_rpc
        result = await backend.run("thread", "start", client_id="client")
        self.assertEqual(result.text, "early result")
        self.assertEqual(result.turn, "next")


if __name__ == "__main__":
    unittest.main()
