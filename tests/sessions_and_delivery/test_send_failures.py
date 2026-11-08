"""Channel recovery counter is independent of retained delivery history."""
import asyncio
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from codex_wechat.app import Bridge
from codex_wechat.errors import WeChatApiError, WeChatNotAcknowledged
from codex_wechat.profiles import restore
from codex_wechat.storage import Store


class SendFailureTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "state")
        self.bridge = Bridge(self.store, {"user": "owner"}, {"backends": {
            "codex": {}, "deepseek": {"command": ["fake-deepseek", "app-server"]}}})
        self.bridge.state["context"] = "context"

    def tearDown(self):
        self.store.lock.close()
        self.temp.cleanup()

    async def test_exhausted_retry_counts_until_confirmed_send_and_keeps_history(self):
        bridge = self.bridge
        bridge.reply("answer")
        bridge.state["outbox"][0]["attempts"] = 2
        reached, release = asyncio.Event(), asyncio.Event()
        calls = 0
        async def send(_):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("simulated channel failure")
            reached.set()
            await release.wait()
        bridge.channel.send = send
        task = asyncio.create_task(bridge.sender())
        try:
            await asyncio.wait_for(reached.wait(), 1)
            self.assertEqual(bridge.prompt_fields()["send_failures"], 1)
            self.assertEqual(self.store.load("bridge")["consecutiveSendFailures"], 1)
            self.assertEqual(len(bridge.state["failedOutbox"]), 1)
            release.set()
            await asyncio.wait_for(bridge.outbox_drained.wait(), 1)
            self.assertEqual(bridge.prompt_fields()["send_failures"], 0)
            self.assertEqual(self.store.load("bridge")["consecutiveSendFailures"], 0)
            self.assertEqual(len(bridge.state["failedOutbox"]), 1)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_retry_diagnostic_matches_configured_wait(self):
        bridge = self.bridge
        bridge.reply("answer")
        calls = 0

        async def send(_):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("simulated channel failure")

        bridge.channel.send = send
        with (patch("codex_wechat.output_delivery.log_event") as log_event,
              patch("codex_wechat.output_delivery.SEND_RETRY_SECONDS", 0)):
            task = asyncio.create_task(bridge.sender())
            try:
                await asyncio.wait_for(bridge.outbox_drained.wait(), 1)
                failed = next(call for call in log_event.call_args_list
                              if call.args and call.args[0] == "output.send_failed")
                self.assertEqual(failed.kwargs["retry_in_ms"], 0)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_api_rejection_is_not_retried_and_waits_for_a_new_context(self):
        bridge = self.bridge
        bridge.reply("answer")
        calls = 0

        async def send(_):
            nonlocal calls
            calls += 1
            raise WeChatApiError(-2, "prepare_failed")

        bridge.channel.send = send
        with patch("codex_wechat.output_delivery.log_event") as log_event:
            task = asyncio.create_task(bridge.sender())
            try:
                for _ in range(50):
                    if bridge.state.get("wechatContextPending"):
                        break
                    await asyncio.sleep(0.02)
                self.assertEqual(calls, 1)  # no retry amplification
                self.assertTrue(bridge.state["wechatContextPending"])
                self.assertEqual(len(bridge.state["failedOutbox"]), 1)
                rejected = next(call for call in log_event.call_args_list
                                if call.args and call.args[0] == "output.send_rejected")
                self.assertEqual(rejected.kwargs["api_error_kind"], "prepare_failed")
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_an_unacknowledged_send_is_quarantined_without_retry(self):
        # A no-id response means delivery is unknown, not that a retry is safe:
        # the first call may already have been delivered. Quarantine it once.
        bridge = self.bridge
        bridge.reply("answer")
        calls = 0

        async def send(_):
            nonlocal calls
            calls += 1
            raise WeChatNotAcknowledged(0)

        bridge.channel.send = send
        with patch("codex_wechat.output_delivery.log_event") as log_event:
            task = asyncio.create_task(bridge.sender())
            try:
                for _ in range(50):
                    if bridge.state.get("failedOutbox"):
                        break
                    await asyncio.sleep(0.02)
                self.assertEqual(calls, 1)  # no automatic resend of an uncertain delivery
                self.assertFalse(bridge.state.get("wechatContextPending"))
                self.assertTrue(bridge.output.paused)
                self.assertEqual(bridge.state.get("deliveryUnknownTotal"), 1)
                self.assertEqual(len(bridge.state["failedOutbox"]), 1)
                self.assertEqual(bridge.state["failedOutbox"][0]["status"], "delivery_unknown")
                self.assertEqual(bridge.state["failedOutbox"][0]["attempts"], 1)
                events = [call.args[0] for call in log_event.call_args_list if call.args]
                self.assertNotIn("output.sent", events)
                self.assertNotIn("output.send_failed", events)
                self.assertIn("output.delivery_unknown", events)
                self.assertIn("output.failed", events)
                self.assertIn("output.send_paused", events)
                paused = next(call for call in log_event.call_args_list
                              if call.args and call.args[0] == "output.send_paused")
                self.assertEqual(paused.kwargs["reason"], "not_acknowledged")
                self.assertEqual(paused.kwargs["release"], "next_inbound")
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_a_dropped_reply_is_quarantined_without_a_failure_notice(self):
        # The channel may be dropping instead of rejecting, so a failure notice
        # would be another uncertain outbound request.
        bridge = self.bridge
        bridge.reply("answer")
        calls = 0

        async def send(_):
            nonlocal calls
            calls += 1
            raise WeChatNotAcknowledged(0)

        bridge.channel.send = send
        with patch("codex_wechat.output_delivery.log_event"):
            task = asyncio.create_task(bridge.sender())
            try:
                for _ in range(50):
                    if bridge.state.get("failedOutbox"):
                        break
                    await asyncio.sleep(0.02)
                self.assertEqual(calls, 1)
                self.assertEqual(len(bridge.state["outbox"]), 0)  # no notice queued
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_no_id_pauses_the_rest_of_the_queue(self):
        bridge = self.bridge
        bridge.reply("one")
        bridge.reply("two")
        attempts = []

        async def send(entry):
            attempts.append(entry)
            if not entry.get("failureNotice"):
                raise WeChatNotAcknowledged(0)

        bridge.channel.send = send
        with patch("codex_wechat.output_delivery.log_event"):
            task = asyncio.create_task(bridge.sender())
            try:
                for _ in range(50):
                    if bridge.state.get("failedOutbox"):
                        break
                    await asyncio.sleep(0.02)
                await asyncio.sleep(0.1)
                self.assertEqual(sum(1 for entry in attempts
                                     if not entry.get("failureNotice")), 1)
                self.assertEqual(len(bridge.state["outbox"]), 1)
                self.assertTrue(bridge.output.paused)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_a_new_inbound_resumes_the_preserved_backlog(self):
        bridge = self.bridge
        bridge.reply("one")
        bridge.reply("two")
        attempts = []

        async def send(entry):
            attempts.append(entry)
            if len([item for item in attempts if not item.get("failureNotice")]) == 1:
                raise WeChatNotAcknowledged(0)
            return {"message_id": len(attempts)}

        bridge.channel.send = send
        with (patch("codex_wechat.output_delivery.log_event") as log_event,
              patch("codex_wechat.output_delivery.SEND_SPACING_SECONDS", 0)):
            task = asyncio.create_task(bridge.sender())
            try:
                for _ in range(50):
                    if bridge.state.get("failedOutbox"):
                        break
                    await asyncio.sleep(0.02)
                self.assertTrue(bridge.output.paused)
                self.assertEqual([entry["text"] for entry in bridge.state["outbox"]],
                                 ["two"])
                bridge.refresh_reply_budget()
                self.assertFalse(bridge.output.paused)
                self.assertEqual([entry["text"] for entry in bridge.state["outbox"]],
                                 ["two"])
                bridge.reply("three")
                for _ in range(50):
                    if not bridge.state["outbox"]:
                        break
                    await asyncio.sleep(0.02)
                self.assertEqual([entry["text"] for entry in attempts
                                  if not entry.get("failureNotice")], ["one", "two", "three"])
                events = [call.args[0] for call in log_event.call_args_list if call.args]
                self.assertNotIn("output.backlog_stale", events)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_api_rejection_kinds_are_not_retried(self):
        bridge = self.bridge
        bridge.reply("answer")
        attempts = []

        async def send(entry):
            attempts.append(entry)
            raise WeChatApiError(-2, "rate_limit_hint")

        bridge.channel.send = send
        with patch("codex_wechat.output_delivery.log_event") as log_event:
            task = asyncio.create_task(bridge.sender())
            try:
                for _ in range(50):
                    if bridge.state.get("failedOutbox"):
                        break
                    await asyncio.sleep(0.02)
                self.assertEqual(sum(1 for entry in attempts
                                     if not entry.get("failureNotice")), 1)
                self.assertFalse(bridge.state.get("wechatContextPending"))
                self.assertIn("output.send_rejected",
                              [call.args[0] for call in log_event.call_args_list
                               if call.args])
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_unclassified_errors_are_not_retried(self):
        bridge = self.bridge
        bridge.reply("answer")
        attempts = []

        async def send(entry):
            attempts.append(entry)
            raise RuntimeError("unexpected local failure")

        bridge.channel.send = send
        with patch("codex_wechat.output_delivery.log_event"):
            task = asyncio.create_task(bridge.sender())
            try:
                for _ in range(50):
                    if bridge.state.get("failedOutbox"):
                        break
                    await asyncio.sleep(0.02)
                self.assertEqual(sum(1 for entry in attempts
                                     if not entry.get("failureNotice")), 1)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_delivery_unknown_pause_is_runtime_only_after_restart(self):
        bridge = self.bridge
        bridge.reply("answer")
        bridge.reply("second")

        async def send(_):
            raise WeChatNotAcknowledged(0)

        bridge.channel.send = send
        with patch("codex_wechat.output_delivery.log_event"):
            task = asyncio.create_task(bridge.sender())
            try:
                for _ in range(50):
                    if bridge.state.get("failedOutbox"):
                        break
                    await asyncio.sleep(0.02)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(len(bridge.state["outbox"]), 1)
        self.assertEqual(bridge.state["failedOutbox"][0]["status"], "delivery_unknown")
        self.assertTrue(bridge.output.paused)

        restarted = Bridge(self.store, {"user": "owner"}, {"backends": {
            "codex": {}, "deepseek": {"command": ["fake-deepseek", "app-server"]}}})
        self.assertEqual(len(restarted.state["outbox"]), 1)
        self.assertEqual(restarted.state["failedOutbox"][0]["status"], "delivery_unknown")
        self.assertEqual(restarted.state["outbox"][0]["text"], "second")
        self.assertFalse(restarted.output.paused)
        sent = []

        async def restarted_send(entry):
            sent.append(entry)
            return {"message_id": len(sent)}

        restarted.channel.send = restarted_send
        task = asyncio.create_task(restarted.sender())
        try:
            for _ in range(50):
                if not restarted.state["outbox"]:
                    break
                await asyncio.sleep(0.02)
            self.assertEqual([entry["text"] for entry in sent], ["second"])
            restarted.reply("fresh")
            for _ in range(50):
                if not restarted.state["outbox"]:
                    break
                await asyncio.sleep(0.02)
            self.assertEqual([entry["text"] for entry in sent], ["second", "fresh"])
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_retry_waits_grow_instead_of_hammering_the_channel(self):
        bridge = self.bridge
        bridge.reply("answer")
        delays = []
        real_sleep = asyncio.sleep

        async def send(_):
            raise OSError("simulated channel failure")

        async def sleep(seconds, *args, **kwargs):
            if seconds:
                delays.append(seconds)
            await real_sleep(0)

        bridge.channel.send = send
        with (patch("codex_wechat.output_delivery.log_event"),
              patch("codex_wechat.output_delivery.asyncio.sleep", sleep)):
            task = asyncio.create_task(bridge.sender())
            try:
                for _ in range(50):
                    if bridge.state.get("failedOutbox"):
                        break
                    await real_sleep(0.02)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(delays[:2], [1, 3])  # 1 s then 3 s, not 1 s twice

    async def test_consecutive_sends_are_spaced(self):
        bridge = self.bridge
        bridge.reply("one")
        bridge.reply("two")
        stamps = []

        async def send(_):
            stamps.append(time.perf_counter())

        bridge.channel.send = send
        with (patch("codex_wechat.output_delivery.log_event"),
              patch("codex_wechat.output_delivery.SEND_SPACING_SECONDS", 0.05)):
            task = asyncio.create_task(bridge.sender())
            try:
                await asyncio.wait_for(bridge.outbox_drained.wait(), 2)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(len(stamps), 2)
        self.assertGreaterEqual(stamps[1] - stamps[0], 0.05)

    def test_status_flags_unconfirmed_delivery_after_repeated_failures(self):
        from codex_wechat.app import status_payload
        for failures, expected in ((0, "ok"), (2, "ok"), (3, "unconfirmed")):
            self.store.save("bridge", {"consecutiveSendFailures": failures})
            self.assertEqual(status_payload(self.store)["delivery"], expected)

    def test_a_past_unknown_delivery_does_not_pin_delivery_to_unconfirmed(self):
        # `delivery` answers "is it healthy now"; the totals answer "what has
        # happened". A recovered channel must read as ok again.
        from codex_wechat.app import status_payload
        self.store.save("bridge", {
            "deliveryUnknownTotal": 1,
            "failedOutbox": [{"status": "delivery_unknown"}],
            "consecutiveSendFailures": 0,
        })
        payload = status_payload(self.store)
        self.assertEqual(payload["delivery"], "ok")
        self.assertEqual(payload["delivery_unknown"], 1)
        self.assertEqual(payload["delivery_unknown_retained"], 1)
        self.store.save("bridge", {
            "deliveryUnknownTotal": 25,
            "failedOutbox": [{"status": "delivery_unknown"}] * 20,
        })
        payload = status_payload(self.store)
        self.assertEqual(payload["delivery_unknown"], 25)
        self.assertEqual(payload["delivery_unknown_retained"], 20)

    async def test_failure_count_is_not_capped_by_session_and_resets_on_restart(self):
        bridge = self.bridge
        for index in range(25):
            entry = bridge.text_entries(str(index), group=str(index), context="context")[0]
            entry["failureNotice"] = True
            bridge.enqueue([entry])
            bridge.fail_output(entry)
        self.assertEqual(len(bridge.state["failedOutbox"]), 20)
        self.assertEqual(bridge.prompt_fields()["send_failures"], 25)
        restore(bridge.state, {"thread": "other"})
        bridge.backend_name = "deepseek"
        bridge.save()
        restarted = Bridge(self.store, {"user": "owner"}, {"backends": {
            "codex": {}, "deepseek": {"command": ["fake-deepseek", "app-server"]}}})
        self.assertEqual(restarted.prompt_fields()["send_failures"], 0)
        self.assertEqual(len(restarted.state["failedOutbox"]), 20)
