"""Frozen output groups, media snapshots and sequential retries.

The bridge is the shared runtime context and sole persistence owner. This component
never copies backend selection or persistent queues across an await.
"""
from dataclasses import asdict
import asyncio
import sys
import time
import uuid
from configuration import completion_template
from records import TurnResult
from slicing import split_text
from wechat import acknowledged_message_id
from media import MediaError, MAX_ATTACHMENTS, split_answer
from wechat_content import entry_summary, remember_quote
from errors import (ErrorAction, WeChatApiError, WeChatAuthExpired,
                    WeChatNotAcknowledged, attachment_reason, disposition)
from diagnostics import current_request_id, error_fields, log_event
from prompts import render_ack

SEND_ATTEMPTS = 3
SEND_RETRY_SECONDS = 1
# Same-user spacing: two production bridges report 2.5 s as the smallest interval
# the platform tolerates without complaints, and bursts are what gets filtered.
SEND_SPACING_SECONDS = 2.5
# A no-id response is persisted as an outbound block and released by the next
# accepted inbound message. A timer must not consume the ordinary backlog.
DELIVERY_DEGRADED_THRESHOLD = 3
# A stale context token is repaired by the next inbound message, not by a retry.
CONTEXT_REJECTION_KINDS = frozenset({"prepare_failed"})

class OutputDelivery:
    def __init__(self, bridge):
        self.bridge = bridge
        self._queued_at = {}  # Monotonic timestamps belong only to this service lifetime.

    def text_entries(self, text, *, group, context, semantic=False, source=None, role="body",
                     request_id=None):
        bridge = self.bridge
        return [{"id": str(uuid.uuid4()), "group": group, "role": role,
                 "user": bridge.account["user"], "context": context, "text": chunk,
                 **({"source": asdict(source)} if source else {}),
                 **({"requestId": request_id} if request_id else {})}
                for chunk in split_text(text, bridge.slicing, semantic=semantic)]

    def enqueue(self, entries, *, persist=True, request_id=None, request_ids=()):
        bridge = self.bridge
        queued_at = time.perf_counter()
        related = tuple(dict.fromkeys(request_ids))
        request_id = request_id or (related[0] if related else current_request_id())
        if request_id:
            for entry in entries:
                entry.setdefault("requestId", request_id)
        for index, entry in enumerate(entries, 1):
            entry.update(part=index, parts=len(entries))
            self._queued_at[entry["id"]] = queued_at
        bridge.state["outbox"].extend(entries)
        if entries:
            log_event("output.enqueued", component="output", count=len(entries),
                      parts=len(entries), queued=len(bridge.state["outbox"]),
                      role=entries[0].get("role", "body"), job_id=entries[0].get("group"),
                      request_id=entries[0].get("requestId"))
            for identity in related:
                if identity != entries[0].get("requestId"):
                    log_event("output.related", component="output",
                              job_id=entries[0].get("group"), request_id=identity)
        if entries:
            bridge.outbox_drained.clear()
        if persist:
            bridge.save()
        bridge.send_wake.set()

    def reply(self, text, *, persist=True, request_id=None):
        bridge = self.bridge
        context = bridge.state.get("context")
        if context or bridge.state.get("wechatContextPending"):
            bridge.enqueue(bridge.text_entries(text, group=str(uuid.uuid4()), context=context,
                                               request_id=request_id),
                         persist=persist, request_id=request_id)

    def deliver_message(self, message):
        """Freeze a whole visible interim message before a turn can complete.

        No disk snapshot awaits here: preserve event order relative to the final
        result, and never retire the input or emit a completion notice early.
        """
        bridge = self.bridge
        if (message.phase not in ("commentary", "partial_answer")
                or not bridge.current_source(message.source)):
            return
        context = bridge.state.get("context")
        if not context and not bridge.state.get("wechatContextPending"):
            return
        bridge.reload_config()
        text, _ = split_answer(message.text)  # Attachments are processed only in the final answer.
        if text.strip():
            bridge.enqueue(bridge.text_entries(text, group=str(uuid.uuid4()), context=context,
                                               semantic=True, source=message.source),
                           request_ids=message.request_ids)

    def asset_entry(self, asset, *, group=None, context=None, source=None, request_id=None):
        bridge = self.bridge
        return {"id": str(uuid.uuid4()), "group": group or str(uuid.uuid4()), "role": "asset",
                "user": bridge.account["user"], "context": context or bridge.state.get("context", ""),
                "asset": asset, **({"source": asdict(source)} if source else {}),
                **({"requestId": request_id} if request_id else {})}

    def queue_asset(self, asset, *, origin=None):
        bridge = self.bridge
        origin = origin or bridge.media_origin()
        bridge.enqueue([bridge.asset_entry(asset, context=origin["context"],
                                             source=origin["source"],
                                             request_id=current_request_id())])

    async def prepare_asset(self, path, kind="auto", *, origin=None):
        """Snapshot a file and run the local checks, without queueing anything."""
        bridge = self.bridge
        origin = origin or bridge.media_origin()
        return await asyncio.to_thread(bridge.media.snapshot, path, origin["cwd"], kind)

    async def deliver_answer(self, answer, *, finish_running=True, cwd=None,
                             notify_completion=False, source=None, require_current=False,
                             request_id=None):
        bridge = self.bridge
        bridge.reload_config()
        request_ids = ()
        turn_result = None
        if isinstance(answer, TurnResult):
            turn_result = answer
            request_ids = answer.request_ids
            source, cwd, notify_completion = answer.source, answer.cwd, answer.completed
            answer = answer.text
        text, specs = split_answer(answer)
        context = bridge.state.get("context")
        if not context and not bridge.state.get("wechatContextPending"):
            return
        group = str(uuid.uuid4())
        assets, errors = [], []
        if len(specs) > MAX_ATTACHMENTS:
            errors.append(bridge.prompt("AUTO_ATTACHMENT_LIMIT", {"count": MAX_ATTACHMENTS}))
        for spec in specs[:MAX_ATTACHMENTS]:
            try:
                asset = await asyncio.to_thread(bridge.media.snapshot, spec["path"],
                    cwd or bridge.backend.info.get("cwd") or bridge.backend.cwd,
                    spec.get("kind", "auto"), automatic=True)
                assets.append(bridge.asset_entry(asset, group=group, context=context,
                                                 source=source, request_id=request_id))
            except (MediaError, ValueError, OSError) as error:
                reason = attachment_reason(error)
                errors.append(bridge.prompt("AUTO_ATTACHMENT_FAILED", {
                    "reason": reason,
                    "hint": (bridge.prompt("AUTO_ATTACHMENT_PATH_HINT")
                             if reason == "只能自动发送当前会话目录内的文件" else ""),
                }))
        # Snapshot work may await disk I/O across a session/backend switch.
        if require_current and not bridge.current_source(source):
            return
        if turn_result is not None and turn_result.completed:
            bridge.remember_turn_result(turn_result)
        entries = bridge.text_entries(text, group=group, context=context, semantic=True,
                                      source=source, request_id=request_id)
        if errors:
            entries.extend(bridge.text_entries("\n".join(errors), group=group, context=context,
                                               source=source, request_id=request_id))
        entries.extend(assets)
        completion_notice = completion_template(bridge.reload_config(), bridge.state)
        if notify_completion and not errors and completion_notice:
            completion = render_ack(completion_notice, bridge.prompt_fields(finishing=finish_running))
            entries.extend(bridge.text_entries(completion, group=group, context=context,
                                               source=source, role="completion",
                                               request_id=request_id))
        if finish_running:
            bridge.state.pop("currentInput", None)
        # The input is retired in the same snapshot as its frozen output group.
        bridge.enqueue(entries, request_id=request_id, request_ids=request_ids)

    async def external_result_worker(self):
        bridge = self.bridge
        while True:
            result = await bridge.external_results.get()
            try:
                if bridge.current_source(result.source):
                    await bridge.deliver_answer(result, finish_running=False, require_current=True)
            except Exception as error:
                bridge.reply(bridge.prompt("RESULT_PREPARE_FAILED"))
                log_event("output.prepare_failed", level="warning", component="output",
                          **error_fields(error))
                print(f"外部结果失败：{type(error).__name__}", file=sys.stderr, flush=True)
            finally:
                bridge.external_results.task_done()

    def fail_output(self, entry, *, notify=True, status="partial"):
        """Freeze a partial group; never present a suffix as a complete answer."""
        bridge = self.bridge
        bridge.state["outbox"].pop(0)
        group = entry.get("group")
        skipped = []
        if group:
            body_failed = entry.get("role", "body") == "body"
            kept = []
            for queued in bridge.state["outbox"]:
                if queued.get("group") == group and (body_failed or queued.get("role") == "completion"):
                    skipped.append(queued)
                else:
                    kept.append(queued)
            bridge.state["outbox"] = kept
        for removed in (entry, *skipped):
            self._queued_at.pop(removed.get("id"), None)
        failed = {**entry, "status": status, "skipped": skipped}
        bridge.state["failedOutbox"] = (bridge.state.get("failedOutbox", []) + [failed])[-20:]
        bridge.state["consecutiveSendFailures"] += 1
        log_event("output.failed", level="warning", component="output",
                  send_failures=bridge.state["consecutiveSendFailures"],
                  role=entry.get("role", "body"), attempt=entry.get("attempts", 0),
                  parts=entry.get("parts"), part=entry.get("part"),
                  count=len(skipped), status=status, job_id=entry.get("group"),
                  request_id=entry.get("requestId"))
        # Never recursively report a failed failure notice, and never spend a
        # request on one when the channel itself is dropping messages. A budget
        # notice gets the same treatment: its replacement would be a notice about
        # a notice, and the queue is already waiting for the user.
        if notify and not entry.get("failureNotice") and not entry.get("budgetNotice"):
            text = (bridge.prompt("ATTACHMENT_SEND_FAILED", {"name": entry["asset"]["name"], "attempts": SEND_ATTEMPTS})
                    if "asset" in entry else bridge.prompt("REPLY_SEND_FAILED"))
            notes = bridge.text_entries(text, group=str(uuid.uuid4()), context=entry["context"],
                                        request_id=entry.get("requestId"))
            for note in notes:
                note["failureNotice"] = True
                self._queued_at[note["id"]] = time.perf_counter()
            bridge.state["outbox"][0:0] = notes
        bridge.save()
        if not bridge.state["outbox"]:
            bridge.outbox_drained.set()

    def budget_notice_entries(self):
        """The one notice that asks the user for the next inbound message."""
        bridge = self.bridge
        pending = sum(1 for entry in bridge.state["outbox"] if not entry.get("budgetNotice"))
        entries = bridge.text_entries(
            bridge.prompt("REPLY_BUDGET_PAUSED", {"count": pending}),
            group=str(uuid.uuid4()), context=bridge.state.get("context", ""), role="notice")
        for entry in entries:
            entry["budgetNotice"] = True
            self._queued_at[entry["id"]] = time.perf_counter()
        return entries

    def charge_reply(self, entry):
        """Charge one reply the server answered; the budget notice is exempt.

        A request that never arrived (timeout, connection failure) is free because
        the platform never saw it; a reply it answered - delivered, silently
        dropped or refused - is not. Charging on success alone let the brake go
        slack exactly while the channel was dropping messages.
        """
        if entry.get("budgetNotice"):
            return
        self.bridge.spend_reply_budget()

    async def _next_sendable(self):
        """Block until an entry may be sent; None means the caller re-checks.

        Two independent gates live here so the sender loop stays about sending:
        an empty queue and the per-inbound reply budget.
        """
        bridge = self.bridge
        if not bridge.state["outbox"]:
            bridge.send_wake.clear()
            await bridge.send_wake.wait()
            return None
        entry = bridge.state["outbox"][0]
        if entry.get("budgetNotice") or not bridge.budget_exhausted():
            return entry
        # The platform counts replies per inbound message; ask for another one
        # instead of spending the notice slot on a dropped send.
        if bridge.state.get("budgetPromptSent"):
            bridge.send_wake.clear()
            await bridge.send_wake.wait()
            return None
        bridge.state["budgetPromptSent"] = True
        bridge.state["outbox"][0:0] = self.budget_notice_entries()
        bridge.save()
        return None

    async def sender(self):
        bridge = self.bridge
        while True:
            if (bridge.wechat_paused or bridge.state.get("wechatContextPending")
                    or bridge.state.get("deliveryPaused")):
                bridge.send_wake.clear()
                await bridge.send_wake.wait()
                continue
            entry = await self._next_sendable()
            if entry is None:
                continue
            try:
                if "asset" in entry and "item" not in entry:
                    entry["item"] = await bridge.media.upload(entry)
                    bridge.save()
                if bridge.wechat_paused:
                    continue
                queued_at = self._queued_at.get(entry.get("id"))
                waiting = ({"queue_wait_ms": max(0, (time.perf_counter() - queued_at) * 1000)}
                           if queued_at is not None and not entry.get("attempts", 0) else {})
                log_event("output.send_start", level="debug", component="output",
                          queued=len(bridge.state["outbox"]), role=entry.get("role", "body"),
                          attempt=entry.get("attempts", 0), part=entry.get("part"),
                          parts=entry.get("parts"), job_id=entry.get("group"),
                          request_id=entry.get("requestId"), **waiting)
                send_started = time.perf_counter()
                try:
                    response = await bridge.channel.send(entry)
                except WeChatApiError:
                    self.charge_reply(entry)
                    raise
                send_ms = (time.perf_counter() - send_started) * 1000
                self.charge_reply(entry)
                message_id = acknowledged_message_id(response)
                if message_id is not None:
                    remember_quote(bridge.state, message_id, entry_summary(entry))
            except WeChatAuthExpired:
                await bridge.require_wechat_rebind()
                continue
            except WeChatApiError as error:
                entry["attempts"] = entry.get("attempts", 0) + 1
                if isinstance(error, WeChatNotAcknowledged):
                    # A no-id response is not a retryable failure: the first call
                    # may already have been delivered, so resending can duplicate
                    # it. Quarantine once and wait for the next inbound message;
                    # a restart must not resume consuming the ordinary backlog.
                    retained = sum(1 for item in bridge.state.get("failedOutbox", [])
                                   if isinstance(item, dict)
                                   and item.get("status") == "delivery_unknown")
                    total = bridge.state.get("deliveryUnknownTotal")
                    if type(total) is not int:
                        total = retained
                    bridge.state["deliveryUnknownTotal"] = max(total, retained) + 1
                    bridge.state["deliveryPaused"] = True
                    log_event("output.delivery_unknown", level="warning",
                              component="output", role=entry.get("role", "body"),
                              attempt=entry["attempts"], error_type=type(error).__name__,
                              error_code=error.code, queued=len(bridge.state["outbox"]),
                              job_id=entry.get("group"), request_id=entry.get("requestId"))
                    bridge.fail_output(entry, notify=False, status="delivery_unknown")
                    log_event("output.send_paused", level="warning", component="output",
                              reason="not_acknowledged",
                              release="next_inbound",
                              send_failures=bridge.state["consecutiveSendFailures"],
                              queued=len(bridge.state["outbox"]))
                    continue
                # Any explicit API rejection is terminal for this payload. A
                # prepare_failed response usually means the context token is stale;
                # hold the queue until a fresh inbound message arrives.
                log_event("output.send_rejected", level="warning", component="output",
                          role=entry.get("role", "body"), attempt=entry["attempts"],
                          error_code=error.code, api_error_kind=error.kind,
                          queued=len(bridge.state["outbox"]),
                          job_id=entry.get("group"), request_id=entry.get("requestId"))
                if error.kind in CONTEXT_REJECTION_KINDS:
                    log_event("output.waiting_context", level="warning", component="output",
                              status="pending", queued=len(bridge.state["outbox"]))
                    bridge.state["wechatContextPending"] = True
                bridge.fail_output(entry)
                continue
            except Exception as error:
                entry["attempts"] = entry.get("attempts", 0) + 1
                if disposition(error).primary is not ErrorAction.RETRYABLE:
                    # Only ordinary transport failures are retryable. An unknown
                    # programming/protocol failure must not be amplified.
                    log_event("output.send_rejected", level="warning", component="output",
                              role=entry.get("role", "body"), attempt=entry["attempts"],
                              error_type=type(error).__name__, queued=len(bridge.state["outbox"]),
                              job_id=entry.get("group"), request_id=entry.get("requestId"))
                    bridge.fail_output(entry)
                    continue
                if entry["attempts"] >= SEND_ATTEMPTS:
                    bridge.fail_output(entry)
                    continue
                bridge.save()
                delay = SEND_RETRY_SECONDS * 3 ** (entry["attempts"] - 1)
                log_event("output.send_failed", level="warning", component="output",
                          role=entry.get("role", "body"), attempt=entry.get("attempts", 0),
                          retry_in_ms=int(delay * 1000), job_id=entry.get("group"),
                          request_id=entry.get("requestId"),
                          **error_fields(error))
                print(f"发送失败（{type(error).__name__}），稍后重试；回复已保存。", file=sys.stderr)
                await asyncio.sleep(delay)
                continue
            bridge.state["outbox"].pop(0)
            self._queued_at.pop(entry.get("id"), None)
            bridge.state["consecutiveSendFailures"] = 0
            log_event("output.sent", component="output", role=entry.get("role", "body"),
                      attempt=entry.get("attempts", 0), duration_ms=send_ms,
                      queued=len(bridge.state["outbox"]), job_id=entry.get("group"),
                      request_id=entry.get("requestId"), **waiting)
            bridge.latency.sends.append(send_ms)
            bridge.state["lastSendMs"] = round(send_ms, 1)
            bridge.mark_wechat_valid()
            bridge.save()
            if not bridge.state["outbox"]:
                bridge.outbox_drained.set()
            elif SEND_SPACING_SECONDS > 0:
                # Space consecutive sends to the same user instead of bursting the
                # whole sliced answer at once.
                await asyncio.sleep(SEND_SPACING_SECONDS)
