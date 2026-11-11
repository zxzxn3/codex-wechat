"""The per-inbound reply budget pauses content and asks for another message."""
import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from codex_wechat.app import Bridge
from codex_wechat.commands import HANDLERS, SETTINGS
from codex_wechat.command_specs import REGISTRY, help_text
from codex_wechat.configuration import Sending
from codex_wechat.records import AcceptedUnknown, Delivered, Rejected, Retryable
from codex_wechat.storage import Store


class SendingConfigTests(unittest.TestCase):
    def test_defaults_to_nine(self):
        self.assertEqual(Sending.from_config({}).reply_budget, 9)
        self.assertEqual(Sending.from_config(None).reply_budget, 9)

    def test_rejects_values_outside_one_to_one_hundred(self):
        for value in (0, 101, True, "3", 3.0, -1):
            with self.assertRaises(ValueError):
                Sending.from_config({"replyBudget": value})
        self.assertEqual(Sending.from_config({"replyBudget": 1}).reply_budget, 1)
        self.assertEqual(Sending.from_config({"replyBudget": 100}).reply_budget, 100)

    def test_rejects_unknown_keys(self):
        with self.assertRaises(ValueError):
            Sending.from_config({"replyBudget": 3, "nope": 1})

    def test_log_retention_is_configured_with_a_floor_and_a_ceiling(self):
        from codex_wechat.configuration import Logs
        self.assertEqual(Logs.from_config(None).keep, 3)
        self.assertEqual(Logs.from_config({}).keep, 3)
        self.assertEqual(Logs.from_config({"keep": 1}).keep, 1)
        self.assertEqual(Logs.from_config({"keep": 100}).keep, 100)
        for value in (0, -1, 101, True, "3", 2.5):
            with self.assertRaises(ValueError):
                Logs.from_config({"keep": value})
        with self.assertRaises(ValueError):
            Logs.from_config({"keep": 3, "maxMiB": 5})

    def test_the_knob_is_settable_but_absent_from_the_panel(self):
        from codex_wechat.prompts import SETTINGS_HELP, SettingsView, settings_summary
        for name in ("budgets", "sending.replyBudget"):
            self.assertEqual(SETTINGS[name].maximum, 100)
            self.assertEqual(SETTINGS[name].path, ("sending", "replyBudget"))
        self.assertNotIn("budgets", SETTINGS_HELP)
        self.assertNotIn("replyBudget", settings_summary(SettingsView(notice="", attachment_mib=20)))


class ReplyBudgetTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "state")
        self.config = {"backends": {"codex": {}}, "sending": {"replyBudget": 2}}
        self.bridge = Bridge(self.store, {"user": "owner"}, self.config)
        self.bridge.state["context"] = "context"

    def tearDown(self):
        self.store.lock.close()
        self.temp.cleanup()

    async def wait_for(self, predicate, tries=200):
        for _ in range(tries):
            if predicate():
                return True
            await asyncio.sleep(0.01)
        return False

    async def test_exhausted_budget_pauses_and_the_notice_asks_for_another_message(self):
        bridge = self.bridge
        for text in ("one", "two", "three", "four"):
            bridge.reply(text)
        sent = []

        async def send(entry):
            sent.append(entry)
            return Delivered(str(len(sent)))

        bridge.channel.send = send
        with patch("codex_wechat.output_delivery.SEND_SPACING_SECONDS", 0):
            task = asyncio.create_task(bridge.sender())
            try:
                self.assertTrue(await self.wait_for(lambda: len(sent) >= 3))
                self.assertEqual([entry["text"] for entry in sent[:2]], ["one", "two"])
                self.assertTrue(sent[2].get("budgetNotice"))
                self.assertEqual(sent[2]["text"], "还有 2 段待发，回复 `/` 继续，或 `/d` 清空。")
                # The notice is spent on top of the budget, so it does not count.
                self.assertEqual(bridge.budget_state()["used"], 2)
                await asyncio.sleep(0.05)
                self.assertEqual(len(sent), 3)  # nothing else until an inbound arrives
                bridge.refresh_reply_budget()
                self.assertTrue(await self.wait_for(lambda: len(sent) >= 5))
                self.assertEqual([entry["text"] for entry in sent[3:]], ["three", "four"])
                self.assertEqual(bridge.state["outbox"], [])
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_inbound_during_send_does_not_charge_the_new_budget(self):
        bridge = self.bridge
        bridge.reply("old")
        entered, release = asyncio.Event(), asyncio.Event()

        async def send(_):
            entered.set()
            await release.wait()
            return Delivered("message-id")

        bridge.channel.send = send
        task = asyncio.create_task(bridge.sender())
        try:
            await asyncio.wait_for(entered.wait(), 1)
            bridge.refresh_reply_budget()
            release.set()
            await asyncio.wait_for(bridge.outbox_drained.wait(), 1)
            self.assertEqual(bridge.budget_state()["seq"], 1)
            self.assertEqual(bridge.budget_state()["used"], 0)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_old_backlog_started_after_a_new_inbound_spends_the_new_budget(self):
        bridge = self.bridge
        for text in ("old one", "old two", "old three"):
            bridge.reply(text)
        bridge.refresh_reply_budget()
        sent = []

        async def send(entry):
            sent.append(entry)
            return Delivered(str(len(sent)))

        bridge.channel.send = send
        with patch("codex_wechat.output_delivery.SEND_SPACING_SECONDS", 0):
            task = asyncio.create_task(bridge.sender())
            try:
                self.assertTrue(await self.wait_for(
                    lambda: any(entry.get("budgetNotice") for entry in sent)))
                self.assertEqual([entry["text"] for entry in sent[:2]],
                                 ["old one", "old two"])
                self.assertEqual(bridge.budget_state()["used"], 2)
                self.assertEqual([entry["text"] for entry in sent[2:]], [
                    "还有 1 段待发，回复 `/` 继续，或 `/d` 清空。"])
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_stale_unknown_delivery_does_not_repause_after_new_inbound(self):
        bridge = self.bridge
        bridge.reply("old")
        entered, release = asyncio.Event(), asyncio.Event()

        async def send(_):
            entered.set()
            await release.wait()
            return AcceptedUnknown(0)

        bridge.channel.send = send
        with patch("codex_wechat.output_delivery.log_event") as log_event:
            task = asyncio.create_task(bridge.sender())
            try:
                await asyncio.wait_for(entered.wait(), 1)
                bridge.refresh_reply_budget()
                release.set()
                self.assertTrue(await self.wait_for(
                    lambda: bridge.state["deliveryUnknownTotal"] == 1))
                self.assertFalse(bridge.output.paused)
                events = [call.args[0] for call in log_event.call_args_list if call.args]
                self.assertIn("output.delivery_unknown", events)
                self.assertNotIn("output.send_paused", events)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_a_request_that_never_arrived_does_not_spend_the_budget(self):
        bridge = self.bridge
        bridge.reply("retried")
        calls = 0

        async def send(_):
            nonlocal calls
            calls += 1
            if calls < 3:
                return Retryable("simulated channel failure")
            return Delivered(str(calls))

        bridge.channel.send = send
        with patch("codex_wechat.output_delivery.log_event"), patch("codex_wechat.output_delivery.SEND_RETRY_SECONDS", 0):
            task = asyncio.create_task(bridge.sender())
            try:
                self.assertTrue(await self.wait_for(lambda: not bridge.state["outbox"]))
                self.assertEqual(calls, 3)  # two failures, one success
                # The failures never reached the server, so only the answer counts.
                self.assertEqual(bridge.budget_state()["used"], 1)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_a_dropped_reply_costs_the_budget_once_and_is_not_retried(self):
        bridge = self.bridge
        bridge.reply("dropped")
        calls = 0

        async def send(_):
            nonlocal calls
            calls += 1
            return AcceptedUnknown(0)

        bridge.channel.send = send
        with patch.object(Bridge, "reply_budget", lambda self: 9), \
                patch("codex_wechat.output_delivery.log_event"):
            task = asyncio.create_task(bridge.sender())
            try:
                self.assertTrue(await self.wait_for(lambda: not bridge.state["outbox"]))
                self.assertEqual(calls, 1)
                # The server answered once; uncertain delivery still spends one
                # request and is not resent automatically.
                self.assertEqual(bridge.budget_state()["used"], 1)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_a_refused_reply_costs_once_and_holds_the_queue(self):
        bridge = self.bridge
        bridge.reply("refused")
        calls = 0

        async def send(_):
            nonlocal calls
            calls += 1
            return Rejected(-2, "prepare_failed")

        bridge.channel.send = send
        with patch("codex_wechat.output_delivery.log_event"):
            task = asyncio.create_task(bridge.sender())
            try:
                self.assertTrue(await self.wait_for(
                    lambda: bridge.state.get("wechatContextPending")))
                self.assertEqual(calls, 1)
                self.assertEqual(bridge.budget_state()["used"], 1)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_only_one_notice_is_queued_per_exhausted_turn(self):
        bridge = self.bridge
        for text in ("one", "two", "three", "four", "five"):
            bridge.reply(text)
        sent = []

        async def send(entry):
            sent.append(entry)
            return Delivered(str(len(sent)))

        bridge.channel.send = send
        with patch("codex_wechat.output_delivery.SEND_SPACING_SECONDS", 0):
            task = asyncio.create_task(bridge.sender())
            try:
                self.assertTrue(await self.wait_for(lambda: len(sent) >= 3))
                await asyncio.sleep(0.05)
                self.assertEqual(sum(1 for entry in sent if entry.get("budgetNotice")), 1)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    def test_spent_budget_survives_a_restart(self):
        self.bridge.spend_reply_budget()
        self.bridge.spend_reply_budget()
        self.bridge.save()
        reloaded = Bridge(self.store, {"user": "owner"}, self.config)
        self.assertEqual(reloaded.budget_state()["used"], 2)
        self.assertTrue(reloaded.budget_exhausted())

    async def test_bare_slash_is_a_silent_command_hidden_from_short_help(self):
        self.assertEqual(REGISTRY["/"].name, "/continue")
        self.assertNotIn("/continue", help_text())
        self.assertIn("/continue", help_text(show_all=True))
        queued = len(self.bridge.state["outbox"])
        await HANDLERS["/continue"](self.bridge, "/continue", "")
        self.assertEqual(len(self.bridge.state["outbox"]), queued)

    def test_a_budget_past_the_ceiling_is_logged_once_with_its_value(self):
        config = {"backends": {"codex": {}}, "sending": {"replyBudget": 20}}
        with patch("codex_wechat.app.log_event") as log_event:
            bridge = Bridge(self.store, {"user": "owner"}, config)
            bridge.reload_config()
            bridge.reload_config()
        events = [call for call in log_event.call_args_list
                  if call.args and call.args[0] == "config.reply_budget_above_ceiling"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].kwargs["level"], "warning")
        self.assertEqual(events[0].kwargs["budget"], 20)
        self.assertEqual(events[0].kwargs["ceiling"], 9)

    def test_the_default_budget_logs_nothing(self):
        with patch("codex_wechat.app.log_event") as log_event:
            Bridge(self.store, {"user": "owner"}, {"backends": {"codex": {}}})
        self.assertNotIn("config.reply_budget_above_ceiling",
                         [call.args[0] for call in log_event.call_args_list if call.args])

    async def test_an_inbound_message_clears_the_pause(self):
        self.bridge.spend_reply_budget()
        self.bridge.spend_reply_budget()
        self.bridge.state["budgetPromptSent"] = True
        self.assertTrue(self.bridge.budget_exhausted())
        self.bridge.refresh_reply_budget()
        self.assertFalse(self.bridge.budget_exhausted())
        self.assertNotIn("budgetPromptSent", self.bridge.state)
        self.assertEqual(self.bridge.budget_state()["used"], 0)
