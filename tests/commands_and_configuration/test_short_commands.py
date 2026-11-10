"""Aliases follow the canonical route and cannot abbreviate protected actions."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from codex_wechat.app import Bridge
from codex_wechat.command_specs import COMMANDS, REGISTRY, command_lane, HELP, HELP_ALL
from codex_wechat.commands import HANDLERS, dispatch, parse_control, validate
from codex_wechat.errors import UserFacingError
from codex_wechat.prompts import SETTINGS_HELP, prompt_text
from codex_wechat.storage import Store
from codex_wechat.version import release_id


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
                            ("/v", "/version"),
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
        self.assertNotIn("`/v`", HELP)
        self.assertNotIn("`/version`", HELP)
        self.assertIn("`/v` `/version`", HELP_ALL)
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

    async def test_version_is_local_and_reports_release_and_backend(self):
        from types import SimpleNamespace

        self.bridge.ensure_backend = AsyncMock()
        self.bridge.backend.transport = SimpleNamespace(server_version="codex_cli_rs/0.167.0")
        with patch("codex_wechat.commands.release_id", return_value="fixture-release"):
            await dispatch(self.bridge, "/v")
        text = self.bridge.state["outbox"][-1]["text"]
        # No product version: the build id is the only identity, so nothing else
        # has to be bumped per release.
        self.assertIn("发布: fixture-release", text)
        self.assertIn("Codex: codex_cli_rs/0.167.0", text)
        self.assertNotIn("微信桥:", text)
        self.bridge.ensure_backend.assert_not_called()

        await dispatch(self.bridge, "/version extra")
        self.assertIn("用法", self.bridge.state["outbox"][-1]["text"])

        self.bridge.backend.transport = None
        with patch("codex_wechat.commands.release_id", return_value=""):
            await dispatch(self.bridge, "/version")
        text = self.bridge.state["outbox"][-1]["text"]
        self.assertIn("发布: 开发版", text)
        self.assertIn("Codex: 未连接", text)

    def test_release_id_reads_manifest_and_ignores_invalid_content(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(release_id(temp), "")
            Path(temp, ".release.json").write_text('{"id": "fixture-release"}', encoding="utf-8")
            self.assertEqual(release_id(temp), "fixture-release")
            Path(temp, ".release.json").write_text("{invalid", encoding="utf-8")
            self.assertEqual(release_id(temp), "")

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
            "`/g` `/get {路径}`\n`/getfile {路径}`\n"
            "例：`/get ./file.png`、`/getfile ./file.zip`",
        )

        await dispatch(self.bridge, "/s invalid")
        self.assertIn("`/s` `/status`", self.bridge.state["outbox"][-1]["text"])

    async def test_help_settings_explains_settings(self):
        await dispatch(self.bridge, "/help settings")
        self.assertEqual(self.bridge.state["outbox"][-1]["text"], SETTINGS_HELP)

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
        self.assertEqual(
            self.bridge.state["outbox"][-1]["text"],
            "已排队: 1 条\n用 `/u` 撤回")
        self.assertTrue(all(lane.empty() for lane in self.bridge.controls.values()))

    async def test_queue_confirmation_plural_counts_only_waiting_tasks(self):
        self.bridge.running = True
        self.bridge.state["currentInput"] = {"id": "active", "text": "running"}
        for number in (1, 2):
            await self.bridge.receive({"msgs": [{
                "message_id": f"queued-{number}", "from_user_id": "owner",
                "message_type": 1, "context_token": "context", "item_list": [
                    {"type": 1, "text_item": {"text": f"/queue work {number}"}}]}]})
            expected = f"已排队: {number} 条\n用 `/u` 撤回"
            self.assertEqual(self.bridge.state["outbox"][-1]["text"], expected)
        self.assertEqual(len(self.bridge.state["inbox"]), 2)


class CommandCatalogContractTests(unittest.TestCase):
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


class InvalidCommandRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_command_does_not_connect(self):
        bridge = SimpleNamespace(reply=lambda _: None, ensure_backend=AsyncMock(),
                                 prompt=prompt_text)
        for command in (
                "/compact bad", "/new bad", "/switch", "/sessions next",
                "/sessions 0", "/missing"):
            await dispatch(bridge, command)
        bridge.ensure_backend.assert_not_awaited()
