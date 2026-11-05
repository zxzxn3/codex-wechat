"""Inbound WeChat normalization, deduplication and command routing."""
import asyncio
import hashlib
import json
import uuid

import wechat_auth
from command_specs import REGISTRY, command_lane
from commands import parse_control
from diagnostics import log_event, new_request_id
from latency import message_timestamps
from media import MAX_ATTACHMENTS
from records import InputPhase
from wechat_content import (message_key, message_summary, message_text,
                            quote_context, quoted_prompt, remember_quote)


class Ingress:
    """Own the WeChat-to-jobs boundary; Bridge remains the persistence owner."""

    def __init__(self, bridge):
        self.bridge = bridge

    async def receive(self, batch):
        bridge = self.bridge
        bridge.reload_config()
        controls = []
        for msg in batch.get("msgs", []):
            request_id = new_request_id()
            if msg.get("from_user_id") != bridge.account["user"] or msg.get("group_id"):
                continue
            # Use item IDs as the protocol fallback; hash only when every ID is absent.
            identity = message_key(msg) or hashlib.sha256(
                json.dumps(msg, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            # Describe transport shape only, including messages skipped before routing.
            # No body, IDs, context tokens or media references enter diagnostics.
            kinds = {item.get("type") for item in msg.get("item_list", [])
                     if isinstance(item, dict) and isinstance(item.get("type"), int)}
            skip = ("unsupported_type" if msg.get("message_type") != 1 else
                    "missing_context" if not msg.get("context_token") else
                    "duplicate" if identity in bridge.state["seen"] else "eligible")
            log_event("input.observed", component="input", request_id=request_id,
                      status=skip, message_type=msg.get("message_type"),
                      message_state=msg.get("message_state"),
                      kind="voice" if 3 in kinds else "text" if 1 in kinds else "other",
                      chars=len(message_text(msg)), count=len(msg.get("item_list", [])))
            if msg.get("message_type") != 1:
                # Type 2 is this bridge's outbound message shape; never answer it.
                if (msg.get("message_type") == 2 or not msg.get("context_token")
                        or identity in bridge.state["seen"]):
                    continue
                wechat_auth.accept_context(bridge.state, msg["context_token"])
                bridge.refresh_reply_budget()
                bridge.state["seen"].append(identity)
                bridge.reject_input(request_id, "unsupported", "unsupported_type")
                bridge.reply(bridge.prompt("UNSUPPORTED_MESSAGE_TYPE"), request_id=request_id)
                continue
            if not msg.get("context_token"):
                # Cannot safely reply without a context token; don't execute.
                continue
            wechat_auth.accept_context(bridge.state, msg["context_token"])
            if identity in bridge.state["seen"]:
                continue
            bridge.state["seen"].append(identity)
            bridge.refresh_reply_budget()
            body_text = message_text(msg)
            quote = quote_context(msg, bridge.state["quoteCache"])
            text = quoted_prompt(body_text, quote)
            attachments = [item for item in msg.get("item_list", [])
                           if item.get("type") in (2, 4, 5)]
            remember_quote(bridge.state, identity, message_summary(msg))
            command, argument = parse_control(body_text)
            spec = REGISTRY.get(command)
            queued = bool(spec and spec.route == "input")
            received = bridge.latency.received_any(message_timestamps(msg))
            if queued:
                text = quoted_prompt(argument, quote)
            kind = ("queued" if queued else
                    "command" if body_text.startswith("/") and not attachments
                    else "input" if text or attachments else "unsupported")
            log_event("input.received", component="input", request_id=request_id,
                      kind=kind, chars=len(text), count=len(attachments),
                      command=spec.name if spec and kind in ("command", "queued") else None)
            if queued and not text and not attachments:
                bridge.reject_input(request_id, kind, "missing_argument")
                bridge.reply(bridge.prompt("COMMAND_USAGE", {"usage": REGISTRY['/queue'].usage}),
                             request_id=request_id)
            elif len(attachments) > MAX_ATTACHMENTS:
                bridge.reject_input(request_id, kind, "too_many_attachments")
                bridge.reply(bridge.prompt("TOO_MANY_ATTACHMENTS", {"count": MAX_ATTACHMENTS}),
                             request_id=request_id)
            elif body_text.startswith("/") and not attachments and not queued:
                received["request_id"] = request_id
                received["command"] = spec.name if spec else None
                if spec and spec.lane == "media":
                    received["media"] = bridge.media_origin()
                controls.append((body_text, received))
            elif (text or attachments) and len(text) <= 20000:
                if len(bridge.state["inbox"]) >= 20:
                    bridge.reject_input(request_id, kind, "queue_full")
                    bridge.reply(bridge.prompt("QUEUE_FULL", {"count": 20}), request_id=request_id)
                else:
                    bridge.state["inbox"].append({"id": identity, "text": text,
                                                  "attachments": attachments,
                                                  "mode": "queue" if queued else "steer",
                                                  "clientId": str(uuid.uuid4()),
                                                  "phase": InputPhase.PREPARING,
                                                  "ingress_ms": received.get("ingress_ms"),
                                                  "requestId": request_id})
                    if queued:
                        count = len(bridge.state["inbox"])
                        bridge.reply(bridge.prompt("QUEUE_CONFIRMED", {
                            "count": count, "unit": "条"}), persist=False,
                            request_id=request_id)
            else:
                status = "too_long" if len(text) > 20000 else "unsupported"
                bridge.reject_input(request_id, kind, status)
                bridge.reply(bridge.prompt("UNSUPPORTED_MESSAGE", {"count": 20000}),
                             request_id=request_id)
        bridge.state["seen"] = bridge.state["seen"][-2000:]
        if batch.get("get_updates_buf"):
            bridge.state["cursor"] = batch["get_updates_buf"]
        # Persist cursor and incoming work together, before starting any agent work.
        bridge.save()
        bridge.send_wake.set()
        for command, received in controls:
            name, argument = parse_control(command)
            queue = bridge.controls[command_lane(name, argument)]
            try:
                queue.put_nowait((command, received))
            except asyncio.QueueFull:
                request_id = received.get("request_id")
                log_event("control.rejected", component="input", kind="command",
                          status="queue_full", request_id=request_id,
                          command=received.get("command"))
                bridge.reply(bridge.prompt("CONTROL_QUEUE_FULL"), request_id=request_id)
        bridge.wake.set()
        bridge.steer_wake.set()
