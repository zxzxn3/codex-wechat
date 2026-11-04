"""Fault-oriented offline tests. All external operations are stubbed."""
import asyncio
from dataclasses import replace
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from codex_wechat import Bridge
from backend import CodexRpcError, DeferTurn
from records import InputPhase, TurnResult
from storage import Store


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.bridge = Bridge(self.store, {"user": "owner"}, {"slicing": {"targetChars": 100}})
        self.bridge.state.update(context="context", thread="t")
        self.tasks = []

    async def asyncTearDown(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.store.lock.close()
        self.temp.cleanup()

    def start(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.append(task)
        return task

    def job(self, identity, phase=InputPhase.PREPARING):
        return {"id": identity, "clientId": "client-" + identity, "text": identity,
                "phase": phase, "attachments": []}

    async def test_queue_prefix_is_persisted_as_input_not_a_control(self):
        b = self.bridge
        def message(identity, text):
            return {"message_id": identity, "from_user_id": "owner", "message_type": 1,
                    "context_token": "context", "item_list": [
                        {"type": 1, "text_item": {"text": text}}]}
        await b.receive({"msgs": [message("q", "/queue /stop is literal text"),
                                  message("s", "correction"), message("empty", "/queue")],
                         "get_updates_buf": "cursor"})
        saved = self.store.load("bridge")
        self.assertEqual([(job["text"], job["mode"]) for job in saved["inbox"]],
                         [("/stop is literal text", "queue"), ("correction", "steer")])
        self.assertTrue(all(queue.empty() for queue in b.controls.values()))
        self.assertEqual(saved["cursor"], "cursor")
        await b.receive({"msgs": [message("q", "/queue duplicate")]})
        self.assertEqual(len(b.state["inbox"]), 2)

    async def test_explicit_queue_never_steers_even_during_desktop_race(self):
        backend = self.bridge.backend
        backend.shared = False
        backend.steer = AsyncMock()
        backend.active = backend.new_turn("t", "desktop", owned=False)
        with self.assertRaises(DeferTurn):
            await backend.run("t", "queued", allow_steer=False)
        backend.steer.assert_not_awaited()
        backend.active = None

        async def racing_inputs(*args):
            backend.active = backend.new_turn("t", "racing", owned=False)
            return []

        backend.input_items = racing_inputs
        with self.assertRaises(DeferTurn):
            await backend.run("t", "queued", allow_steer=False)
        backend.steer.assert_not_awaited()

    async def test_queued_input_does_not_block_later_steering(self):
        b = self.bridge
        b.backend.active = b.backend.new_turn("t", "active", owned=False)
        queued, correction = self.job("q"), self.job("s")
        queued["mode"], correction["mode"] = "queue", "steer"
        b.state["inbox"] = [queued, correction]
        b.media.incoming = AsyncMock(return_value=[])
        b.backend.steer = AsyncMock(return_value=True)
        self.start(b.steering_worker())
        b.steer_wake.set()
        await asyncio.wait_for(b.wake.wait(), 1)
        self.assertEqual([job["id"] for job in b.state["inbox"]], ["q"])
        self.assertEqual(b.backend.steer.call_args.args[2], "s")

    async def test_recovery_preserves_preflight_and_quarantines_submitted(self):
        b = self.bridge
        b.state["currentInput"] = self.job("preflight")
        b.state["inbox"] = [self.job("unknown", InputPhase.DISPATCHING),
                            self.job("accepted", InputPhase.ACCEPTED), self.job("queued")]
        b.recover_inputs()
        self.assertEqual([j["id"] for j in b.state["inbox"]], ["preflight", "queued"])
        self.assertEqual([j["phase"] for j in b.state["inputRecords"]], ["uncertain", "accepted"])
        self.assertNotIn("currentInput", b.state)
        self.assertEqual(self.store.load("bridge")["inbox"], b.state["inbox"])

    async def test_worker_crash_during_preparation_keeps_current_and_pending(self):
        b = self.bridge
        b.loaded = True
        b.ensure_backend = AsyncMock()
        b.state["inbox"] = [self.job("first"), self.job("second")]
        entered = asyncio.Event()

        async def download(_):
            entered.set()
            await asyncio.Event().wait()

        b.media.incoming = download
        task = self.start(b.worker())
        await asyncio.wait_for(entered.wait(), 1)
        saved = self.store.load("bridge")
        self.assertEqual(saved["currentInput"]["id"], "first")
        self.assertEqual(saved["currentInput"]["phase"], "preparing")
        self.assertEqual([job["id"] for job in saved["inbox"]], ["second"])
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        b.recover_inputs()
        self.assertEqual([job["id"] for job in b.state["inbox"]], ["first", "second"])

    async def test_stop_during_bridge_preparation_never_calls_model(self):
        b = self.bridge
        b.loaded = True
        b.ensure_backend = AsyncMock()
        b.backend.run = AsyncMock()
        b.state["inbox"] = [self.job("first")]
        entered, release = asyncio.Event(), asyncio.Event()

        async def download(_):
            entered.set()
            await release.wait()
            return []

        b.media.incoming = download
        self.start(b.worker())
        await asyncio.wait_for(entered.wait(), 1)
        await b.control("/stop")
        release.set()
        # worker sets wake only after retiring the stopped current input.
        b.wake.clear()
        await asyncio.wait_for(b.wake.wait(), 1)
        b.backend.run.assert_not_awaited()
        self.assertNotIn("currentInput", self.store.load("bridge"))

    async def test_input_submission_snapshot_precedes_rpc(self):
        b = self.bridge
        backend = b.backend
        backend.shared = False
        job = self.job("incoming")
        b.state["currentInput"] = job
        backend.selected_thread = "t"
        backend.input_items = AsyncMock(return_value=[])

        async def rpc(method, params):
            self.assertEqual(method, "turn/start")
            saved = self.store.load("bridge")["currentInput"]
            self.assertEqual(saved["phase"], "dispatching")
            self.assertEqual(params["clientUserMessageId"], saved["clientId"])
            raise TimeoutError()

        backend.rpc = rpc
        with self.assertRaises(asyncio.TimeoutError):
            await backend.run("t", "input", client_id=job["clientId"],
                dispatch=lambda phase, turn: b.dispatch_input(job, phase, turn, backend))
        b.recover_inputs()
        self.assertFalse(b.state["inbox"])
        self.assertEqual(b.state["inputRecords"][-1]["phase"], "uncertain")

    async def test_internal_steer_error_is_not_safe_to_requeue(self):
        backend = self.bridge.backend
        backend.active = backend.new_turn("t", "turn", owned=False)
        backend.rpc = AsyncMock(side_effect=CodexRpcError(-32603, "internal failure"))
        with self.assertRaises(CodexRpcError):
            await backend.steer("t", "turn", "input")

    async def test_local_controls_and_stop_bypass_slow_backend_command(self):
        b = self.bridge
        entered = asyncio.Event()

        async def unavailable():
            entered.set()
            await asyncio.Event().wait()

        b.ensure_backend = unavailable
        for lane in b.controls:
            self.start(b.control_worker(lane))
        b.controls["backend"].put_nowait(("/sessions", None))
        await asyncio.wait_for(entered.wait(), 1)
        b.state["inbox"] = [self.job("queued")]
        b.controls["local"].put_nowait(("/ping", {"ingress_ms": 12}))
        b.controls["urgent"].put_nowait(("/stop", None))
        await asyncio.wait_for(b.controls["local"].join(), 1)
        await asyncio.wait_for(b.controls["urgent"].join(), 1)
        self.assertTrue(any(e.get("text", "").startswith("微信 ─") for e in b.state["outbox"]))
        self.assertFalse(b.state["inbox"])

    async def test_late_results_do_not_cross_selection_or_connection(self):
        b = self.bridge
        self.start(b.external_result_worker())
        current = b.backend.source("t")
        for source in (replace(current, backend="other"), replace(current, connection="old"),
                       replace(current, selection=current.selection + 1)):
            b.external_results.put_nowait(TurnResult(source, "turn", "wrong", self.temp.name, "completed"))
        await asyncio.wait_for(b.external_results.join(), 1)
        self.assertFalse(b.state["outbox"])
        b.external_results.put_nowait(TurnResult(current, "turn", "right", self.temp.name, "completed"))
        await asyncio.wait_for(b.external_results.join(), 1)
        self.assertEqual(b.state["outbox"][0]["text"], "right")

    async def test_middle_failure_stops_group_but_preserves_next_reply(self):
        b = self.bridge
        await b.deliver_answer("\n\n".join("段" * 80 for _ in range(4)))
        entries = list(b.state["outbox"])
        self.assertGreater(len(entries), 2)
        b.reply("下一条独立回复")
        b.state["outbox"].pop(0)  # First part already sent.
        b.fail_output(b.state["outbox"][0])
        self.assertEqual(b.state["outbox"][-1]["text"], "下一条独立回复")
        self.assertFalse(any(e.get("group") == entries[0]["group"] for e in b.state["outbox"]))
        failed = self.store.load("bridge")["failedOutbox"][-1]
        self.assertEqual(failed["status"], "partial")
        self.assertTrue(failed["skipped"])
        self.assertEqual(failed["part"], 2)

    async def test_asset_failure_suppresses_completion_but_not_other_assets(self):
        b = self.bridge
        b.enqueue([b.asset_entry({"name": "one"}, group="g"),
                   b.asset_entry({"name": "two"}, group="g"),
                   *b.text_entries("完成", group="g", context="context", role="completion")])
        b.fail_output(b.state["outbox"][0])
        self.assertTrue(any(e.get("asset", {}).get("name") == "two" for e in b.state["outbox"]))
        self.assertFalse(any(e.get("role") == "completion" for e in b.state["outbox"]))

    async def test_rate_limit_cache_survives_thread_change(self):
        backend = self.bridge.backend
        backend.shared = False
        limits = {"rateLimits": {"primary": {"usedPercent": 12}}}
        backend.account_limits = limits
        backend.rpc = AsyncMock(return_value={"thread": {"id": "new"}})
        await backend.thread("new")
        self.assertEqual(backend.account_limits, limits)

    async def test_successful_send_persists_last_latency_sample(self):
        b = self.bridge
        b.reply("one")
        b.channel.send = AsyncMock(return_value={"message_id": "m"})
        task = self.start(b.sender())
        await asyncio.wait_for(b.outbox_drained.wait(), 1)
        saved = self.store.load("bridge")
        self.assertIn("lastSendMs", saved)
        self.assertGreaterEqual(saved["lastSendMs"], 0)
        task.cancel()

    async def test_a_retried_message_repeats_the_receipt_as_it_backs_off(self):
        # The repeated receipt is the user-visible "still trying" signal, so the
        # retry cadence - not the receipt - is what has to stay quiet.
        self.bridge.backend.transport.proc = None
        job = self.job("a")
        self.bridge.acknowledge(job)
        self.bridge.acknowledge(job)
        self.assertEqual(len(self.bridge.state["outbox"]), 2)
        delays = []
        with patch("input_delivery.time.time", return_value=1000.0):
            for _ in range(4):
                self.assertTrue(self.bridge.inputs.queue_writer_conflict(self.bridge, job))
                delays.append(job["writerRetryAt"] - 1000.0)
        self.assertEqual(delays, [30, 60, 120, 120])

    async def test_usage_before_resume_response_is_kept_per_thread(self):
        backend = self.bridge.backend
        backend.shared = False
        backend.selected_thread = "old"
        backend.info = {"id": "old", "usage": {"last": {"totalTokens": 99}}}
        usage = {"last": {"totalTokens": 1000}, "modelContextWindow": 200000}

        async def resume(method, params):
            backend.event({"method": "thread/tokenUsage/updated", "params": {
                "threadId": "new", "tokenUsage": usage}})
            self.assertEqual(backend.info["usage"]["last"]["totalTokens"], 99)
            return {"thread": {"id": "new"}}

        backend.rpc = resume
        await backend.thread("new")
        self.assertEqual(backend.info["usage"], usage)
