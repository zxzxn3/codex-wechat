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
from media import MediaError, MAX_ATTACHMENTS, split_answer
from errors import attachment_reason
from prompts import ATTACHMENT_SEND_FAILED, AUTO_ATTACHMENT_FAILED, AUTO_ATTACHMENT_LIMIT, AUTO_ATTACHMENT_PATH_HINT, fill, render_ack

SEND_ATTEMPTS = 3

class OutputDelivery:
    def __init__(self, bridge):
        self.bridge = bridge

    def text_entries(self, text, *, group, context, semantic=False, source=None, role="body"):
        bridge = self.bridge
        return [{"id": str(uuid.uuid4()), "group": group, "role": role,
                 "user": bridge.account["user"], "context": context, "text": chunk,
                 **({"source": asdict(source)} if source else {})}
                for chunk in split_text(text, bridge.slicing, semantic=semantic)]

    def enqueue(self, entries, *, persist=True):
        bridge = self.bridge
        for index, entry in enumerate(entries, 1):
            entry.update(part=index, parts=len(entries))
        bridge.state["outbox"].extend(entries)
        if entries:
            bridge.outbox_drained.clear()
        if persist:
            bridge.save()
        bridge.send_wake.set()

    def reply(self, text, *, persist=True):
        bridge = self.bridge
        context = bridge.state.get("context")
        if context:
            bridge.enqueue(bridge.text_entries(text, group=str(uuid.uuid4()), context=context),
                         persist=persist)

    def asset_entry(self, asset, *, group=None, context=None, source=None):
        bridge = self.bridge
        return {"id": str(uuid.uuid4()), "group": group or str(uuid.uuid4()), "role": "asset",
                "user": bridge.account["user"], "context": context or bridge.state["context"],
                "asset": asset, **({"source": asdict(source)} if source else {})}

    async def queue_file(self, path, kind="auto", *, origin=None):
        bridge = self.bridge
        origin = origin or bridge.media_origin()
        cwd, context, source = origin["cwd"], origin["context"], origin["source"]
        asset = await asyncio.to_thread(bridge.media.snapshot, path, cwd, kind)
        bridge.enqueue([bridge.asset_entry(asset, context=context, source=source)])

    async def deliver_answer(self, answer, *, finish_running=True, cwd=None,
                             notify_completion=False, source=None, require_current=False):
        bridge = self.bridge
        bridge.reload_config()
        if isinstance(answer, TurnResult):
            source, cwd, notify_completion = answer.source, answer.cwd, answer.completed
            answer = answer.text
        text, specs = split_answer(answer)
        context = bridge.state.get("context")
        if not context:
            return
        group = str(uuid.uuid4())
        assets, errors = [], []
        if len(specs) > MAX_ATTACHMENTS:
            errors.append(fill(AUTO_ATTACHMENT_LIMIT, {"数量": MAX_ATTACHMENTS}))
        for spec in specs[:MAX_ATTACHMENTS]:
            try:
                asset = await asyncio.to_thread(bridge.media.snapshot, spec["path"],
                    cwd or bridge.backend.info.get("cwd") or bridge.backend.cwd,
                    spec.get("kind", "auto"), automatic=True)
                assets.append(bridge.asset_entry(asset, group=group, context=context, source=source))
            except (MediaError, ValueError, OSError) as error:
                reason = attachment_reason(error)
                errors.append(fill(AUTO_ATTACHMENT_FAILED, {
                    "原因": reason,
                    "建议": (AUTO_ATTACHMENT_PATH_HINT
                             if reason == "只能自动发送当前会话目录内的文件" else ""),
                }))
        # Snapshot work may await disk I/O across a session/backend switch.
        if require_current and not bridge.current_source(source):
            return
        entries = bridge.text_entries(text, group=group, context=context, semantic=True, source=source)
        if errors:
            entries.extend(bridge.text_entries("\n".join(errors), group=group, context=context, source=source))
        entries.extend(assets)
        completion_notice = completion_template(bridge.reload_config(), bridge.state)
        if notify_completion and not errors and completion_notice:
            completion = render_ack(completion_notice, bridge.prompt_fields(finishing=finish_running))
            entries.extend(bridge.text_entries(completion, group=group, context=context,
                                             source=source, role="completion"))
        if finish_running:
            bridge.state.pop("currentInput", None)
        # The input is retired in the same snapshot as its frozen output group.
        bridge.enqueue(entries)

    async def external_result_worker(self):
        bridge = self.bridge
        while True:
            result = await bridge.external_results.get()
            try:
                if bridge.current_source(result.source):
                    await bridge.deliver_answer(result, finish_running=False, require_current=True)
            except Exception as error:
                bridge.reply("结果准备失败，请在桌面查看该轮回答。")
                print(f"外部结果失败：{type(error).__name__}", file=sys.stderr, flush=True)
            finally:
                bridge.external_results.task_done()

    def fail_output(self, entry):
        bridge = self.bridge
        """Freeze a partial group; never present a suffix as a complete answer."""
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
        failed = {**entry, "status": "partial", "skipped": skipped}
        bridge.state["failedOutbox"] = (bridge.state.get("failedOutbox", []) + [failed])[-20:]
        # Never recursively report a failed failure notice.
        if not entry.get("failureNotice"):
            text = (fill(ATTACHMENT_SEND_FAILED, {"名称": entry["asset"]["name"], "次数": SEND_ATTEMPTS})
                    if "asset" in entry else "这条回复未能完整发送，已停止其余部分。请在桌面查看。")
            notes = bridge.text_entries(text, group=str(uuid.uuid4()), context=entry["context"])
            for note in notes:
                note["failureNotice"] = True
            bridge.state["outbox"][0:0] = notes
        bridge.save()
        if not bridge.state["outbox"]:
            bridge.outbox_drained.set()

    async def sender(self):
        bridge = self.bridge
        while True:
            if not bridge.state["outbox"]:
                bridge.send_wake.clear()
                await bridge.send_wake.wait()
                continue
            entry = bridge.state["outbox"][0]
            try:
                if "asset" in entry and "item" not in entry:
                    entry["item"] = await bridge.media.upload(entry)
                    bridge.save()
                send_started = time.perf_counter()
                await bridge.channel.send(entry)
                send_ms = (time.perf_counter() - send_started) * 1000
            except Exception as error:
                entry["attempts"] = entry.get("attempts", 0) + 1
                if entry["attempts"] >= SEND_ATTEMPTS:
                    bridge.fail_output(entry)
                    continue
                bridge.save()
                print(f"发送失败（{type(error).__name__}），稍后重试；回复已保存。", file=sys.stderr)
                await asyncio.sleep(5)
                continue
            bridge.state["outbox"].pop(0)
            bridge.latency.sends.append(send_ms)
            bridge.save()
            if not bridge.state["outbox"]:
                bridge.outbox_drained.set()
