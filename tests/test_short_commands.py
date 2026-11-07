"""Aliases follow the canonical route and cannot abbreviate protected actions."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from codex_wechat.app import Bridge
from codex_wechat.command_specs import REGISTRY, command_lane, HELP, HELP_ALL
from codex_wechat.commands import dispatch
from codex_wechat.prompts import SETTINGS_HELP
from codex_wechat.storage import Store


class ShortCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "state")
        self.bridge = Bridge(self.store, {"user": "owner"}, {})
        self.bridge.state["context"] = "context"

    async def asyncTearDown(self):
        self.store.lock.close()
        self.temp.cleanup()

    async def test_aliases_and_protected_commands_share_one_registry(self):
        for short, full in (("/s", "/status"), ("/q", "/queue"), ("/m", "/model"),
                            ("/e", "/effort"), ("/p", "/ping"), ("/h", "/help"),
                            ("/a", "/answer"), ("/n", "/new"), ("/c", "/compact"),
                            ("/u", "/unqueue"), ("/b", "/backend"), ("/r", "/restart"),
                            ("/d", "/discard"), ("/g", "/get"), ("/", "/continue")):
            self.assertIs(REGISTRY[short], REGISTRY[full])
            self.assertEqual(command_lane(short, ""), command_lane(full, ""))
            self.assertIn(f"`{short}`", HELP_ALL)
        for name in ("/settings", "/sessions", "/switch", "/stop", "/cwd",
                     "/approve", "/getfile"):
            self.assertFalse(REGISTRY[name].aliases)
        self.assertNotIn("/ack", REGISTRY)
        self.assertNotIn("/finish", REGISTRY)
        self.assertIn("`/settings` 查看设置", HELP)
        self.assertIn("`/help settings` 查看设置说明", HELP)
        self.assertIn("`/d` `/discard` 清空待发送消息", HELP)
        self.assertNotIn("`/help [all]`", HELP)
        self.assertIn("`/deny {编号}` 拒绝审批", HELP_ALL)
        self.assertNotIn("`/d` `/deny`", HELP_ALL)

    async def test_status_alias_is_local_and_rejects_extra_args(self):
        self.bridge.ensure_backend = AsyncMock()
        await dispatch(self.bridge, "/s")
        self.assertIn("状态", self.bridge.state["outbox"][-1]["text"])
        await dispatch(self.bridge, "/s invalid")
        self.assertIn("用法", self.bridge.state["outbox"][-1]["text"])
        self.bridge.ensure_backend.assert_not_called()

    async def test_help_all_lists_the_complete_catalogue_and_rejects_other_arguments(self):
        self.bridge.ensure_backend = AsyncMock()
        await dispatch(self.bridge, "/help")
        self.assertEqual(self.bridge.state["outbox"][-1]["text"], HELP)
        await dispatch(self.bridge, "/h all")
        self.assertEqual(self.bridge.state["outbox"][-1]["text"], HELP_ALL)
        await dispatch(self.bridge, "/help invalid")
        self.assertIn("`/h` `/help [all|settings]`",
                      self.bridge.state["outbox"][-1]["text"])
        self.bridge.ensure_backend.assert_not_called()

    async def test_alias_leads_command_view_and_usage_hints(self):
        self.bridge.ensure_backend = AsyncMock()
        self.bridge.loaded = True
        self.bridge.backend.info = {"model": "fixture", "effort": "high"}
        self.bridge.backend.models = AsyncMock(return_value=[{
            "model": "fixture",
            "supportedReasoningEfforts": [
                {"reasoningEffort": "low"}, {"reasoningEffort": "high"}],
        }])

        await dispatch(self.bridge, "/e")
        self.assertIn("`/e` `/effort {档位}`", self.bridge.state["outbox"][-1]["text"])

        await dispatch(self.bridge, "/get")
        self.assertEqual(
            self.bridge.state["outbox"][-1]["text"],
            "`/g` `/get {路径}`\n`/getfile {路径}`",
        )

        await dispatch(self.bridge, "/s invalid")
        self.assertIn("`/s` `/status`", self.bridge.state["outbox"][-1]["text"])

    async def test_help_settings_explains_settings(self):
        await dispatch(self.bridge, "/help settings")
        self.assertEqual(self.bridge.state["outbox"][-1]["text"], SETTINGS_HELP)

    def test_notices_take_command_references_from_the_registry(self):
        # A notice that spells a command itself cannot follow a rename, so these
        # interpolate `{<command>_cmd}` instead of naming the command.
        from codex_wechat.command_specs import command_fields
        from codex_wechat.prompts import DEFAULT_PROMPTS
        notices = ("BUSY_CHANGE", "BUSY_RESTART", "STOP_UNCONFIRMED", "TASK_FAILED",
                   "TURN_STALLED", "TURN_FAILED_CONTEXT", "TURN_FAILED_MODEL_NOT_FOUND",
                   "TURN_FAILED_SANDBOX", "TURN_ENDED_UNEXPECTEDLY",
                   "AUTO_ATTACHMENT_PATH_HINT", "SESSION_INDEX_INVALID",
                   "SESSION_SEARCH_MULTIPLE", "SESSION_SEARCH_MORE", "CONTROL_TIMEOUT",
                   "CONTROL_FAILED", "BACKEND_UNREADY", "REPLY_BUDGET_PAUSED",
                   "FAILURE_COMPACT")
        fields = command_fields()
        for name in notices:
            template = DEFAULT_PROMPTS[name]
            with self.subTest(notice=name):
                self.assertNotIn("`/", template)
                self.assertTrue(any(f"{{{key}}}" in template for key in fields))

    async def test_dispatch_and_standalone_backend_fill_command_references(self):
        # Two render paths exist: the bridge (hot prompt overrides) and the
        # backend's own default for standalone use. Both must fill the same fields.
        from codex_wechat.backend import render_prompt
        for name in ("TURN_FAILED_CONTEXT", "REPLY_BUDGET_PAUSED"):
            with self.subTest(notice=name):
                rendered = (self.bridge.prompt(name, {"count": 2}),
                            render_prompt(name, {"count": 2}))
                self.assertEqual(rendered[0], rendered[1])
                self.assertNotIn("_cmd}", rendered[0])
                self.assertIn("`/", rendered[0])

    def test_control_lane_queues_are_bounded_by_the_shared_gate_only(self):
        # Ingress refuses past PENDING_CONTROL_LIMIT, so a second per-lane cap could
        # never be reached; it would only add a failure path nobody can trigger.
        for queue in self.bridge.controls.values():
            self.assertEqual(queue.maxsize, 0)

    async def test_model_alias_passes_arguments_to_canonical_handler(self):
        handler = AsyncMock()
        self.bridge.ensure_backend = AsyncMock()
        with patch.dict("codex_wechat.commands.HANDLERS", {"/model": handler}):
            await dispatch(self.bridge, "/m 2")
        handler.assert_awaited_once_with(self.bridge, "/model", "2", None)
        self.bridge.ensure_backend.assert_awaited_once()

    async def test_queue_alias_strips_prefix_and_preserves_literal_command(self):
        await self.bridge.receive({"msgs": [{"message_id": "q", "from_user_id": "owner",
            "message_type": 1, "context_token": "context", "item_list": [
                {"type": 1, "text_item": {"text": "/q /stop is literal"}}]}]})
        jobs = self.store.load("bridge")["inbox"]
        self.assertEqual([(job["text"], job["mode"]) for job in jobs], [("/stop is literal", "queue")])
        self.assertEqual(self.bridge.state["outbox"][-1]["text"], "已排队: 1 条")
        self.assertTrue(all(lane.empty() for lane in self.bridge.controls.values()))

    async def test_queue_confirmation_plural_counts_only_waiting_tasks(self):
        self.bridge.running = True
        self.bridge.state["currentInput"] = {"id": "active", "text": "running"}
        for number in (1, 2):
            await self.bridge.receive({"msgs": [{
                "message_id": f"queued-{number}", "from_user_id": "owner",
                "message_type": 1, "context_token": "context", "item_list": [
                    {"type": 1, "text_item": {"text": f"/queue work {number}"}}]}]})
            expected = f"已排队: {number} 条"
            self.assertEqual(self.bridge.state["outbox"][-1]["text"], expected)
        self.assertEqual(len(self.bridge.state["inbox"]), 2)
