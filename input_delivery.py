"""Input preparation, steering and recovery transitions.

The bridge is the shared runtime context and sole persistence owner. This component
never copies backend selection or persistent queues across an await.
"""
from lifecycle import can_prepare_input, can_steer_input
from dataclasses import asdict
import asyncio
import functools
import sys
import time
import uuid
from backend import CodexRpcError, DeferTurn, TurnStalled, WRITER_RELEASED
from commands import apply_pending_settings, ensure_session
from records import InputPhase
from media import MediaError
from errors import attachment_reason
from diagnostics import error_fields, log_event

class InputDelivery:
    # While idle with a released thread, watch for a desktop that takes it over.
    FOLLOW_POLL_SECONDS = 5
    FOLLOW_RETRY_SECONDS = 60
    # A desktop holding the thread is usually a user action, not an error: keep
    # the message queued until it lets go, but never forever.
    WRITER_WAIT_SECONDS = 600
    WRITER_RETRY_SECONDS = 30
    # Retries double from the first interval up to this cap, so a two-minute lock
    # costs a few attempts instead of four, and each attempt repeats the receipt.
    WRITER_RETRY_MAX_SECONDS = 120

    def __init__(self, bridge):
        self.bridge = bridge
        self.next_follow_at = 0.0

    def awaiting_desktop(self):
        backend = self.bridge.backend
        thread = self.bridge.state.get("thread")
        return bool(thread and backend.writer_thread == thread
                    and backend.writer_mode == WRITER_RELEASED)

    def writer_wait_left(self, job):
        retry_at = job.get("writerRetryAt")
        if not isinstance(retry_at, (int, float)):
            return 0
        return max(0.0, retry_at - time.time())

    def queue_writer_conflict(self, bridge, job):
        """Keep the message while the desktop holds the thread, then give up."""
        now = time.time()
        queued_at = job.get("writerQueuedAt")
        if not isinstance(queued_at, (int, float)):
            queued_at = job["writerQueuedAt"] = now
        if now - queued_at > self.WRITER_WAIT_SECONDS:
            return False
        attempts = job.get("writerAttempts")
        attempts = 1 if not isinstance(attempts, int) else attempts + 1
        job["writerAttempts"] = attempts
        job["writerRetryAt"] = now + min(
            self.WRITER_RETRY_MAX_SECONDS,
            self.WRITER_RETRY_SECONDS * 2 ** min(attempts - 1, 8))
        job["phase"] = InputPhase.PREPARING
        job["mode"] = "queue"
        if bridge.state.get("currentInput") is job:
            bridge.state.pop("currentInput")
        if not any(item is job for item in bridge.state["inbox"]):
            bridge.state["inbox"].insert(0, job)
        if not job.get("writerNotified"):
            job["writerNotified"] = True
            bridge.reply(bridge.prompt("SESSION_WRITER_QUEUED"),
                         request_id=job.get("requestId"))
        log_event("input.writer_wait", component="input", status="queue",
                  phase=job["phase"], mode="queue", request_id=job.get("requestId"))
        bridge.save()
        return True

    async def follow_desktop(self):
        """Re-attach to the App once it owns our idle thread; else back off."""
        backend = self.bridge.backend
        if time.monotonic() < self.next_follow_at or not backend.app_available():
            return
        if not await backend.follow_app(self.bridge.state.get("thread")):
            self.next_follow_at = time.monotonic() + self.FOLLOW_RETRY_SECONDS

    def dispatch_input(self, job, phase, turn_id, backend):
        bridge = self.bridge
        if phase == InputPhase.DISPATCHING and bridge.wechat_paused:
            job["authDeferred"] = True
            log_event("input.deferred", component="input", status="auth",
                      phase=phase, mode=job.get("mode", "steer"),
                      request_id=job.get("requestId"))
            return False
        if phase == InputPhase.DISPATCHING and bridge.restarting:
            job["restartDeferred"] = True
            log_event("input.deferred", component="input", status="restart",
                      phase=phase, mode=job.get("mode", "steer"),
                      request_id=job.get("requestId"))
            return False
        if phase == InputPhase.DISPATCHING and (bridge.stopping or backend is not bridge.backend):
            log_event("input.deferred", component="input",
                      status="stopping" if bridge.stopping else "backend_changed",
                      phase=phase, mode=job.get("mode", "steer"),
                      request_id=job.get("requestId"))
            return False
        job.update(phase=phase, turn=turn_id, source=asdict(backend.source(bridge.state["thread"])))
        current = backend.active
        request_id = job.get("requestId")
        if (phase in (InputPhase.DISPATCHING, InputPhase.ACCEPTED) and request_id and current
                and current["thread"] == bridge.state["thread"] and current["turn"] == turn_id):
            related = current.setdefault("requestIds", [])
            if request_id not in related:
                related.append(request_id)
        bridge.save()
        log_event("input.dispatch", component="input", phase=phase,
                  mode=job.get("mode", "steer"), backend=bridge.backend_name,
                  request_id=job.get("requestId"))
        return True

    def record_input(self, job, phase):
        bridge = self.bridge
        job["phase"] = phase
        bridge.state["inputRecords"] = (bridge.state.get("inputRecords", []) + [dict(job)])[-20:]

    def recover_inputs(self):
        bridge = self.bridge
        legacy_running = bridge.state.pop("running", False)
        bridge.state.pop("phase", None)
        current = bridge.state.pop("currentInput", None)
        jobs = ([current] if current else []) + bridge.state["inbox"]
        bridge.state["inbox"] = []
        uncertain = False
        for job in jobs:
            phase = job.get("phase", InputPhase.PREPARING)
            if job.get("dispatching") or phase in (InputPhase.DISPATCHING, InputPhase.ACCEPTED):
                bridge.record_input(job, InputPhase.ACCEPTED if phase == InputPhase.ACCEPTED else InputPhase.UNCERTAIN)
                uncertain = True
            else:
                job["phase"] = InputPhase.PREPARING
                job.pop("ingress_ms", None)  # Previous service lifetime is not a network sample.
                job.pop("restartDeferred", None)
                job.pop("authDeferred", None)
                job.setdefault("clientId", str(uuid.uuid4()))
                bridge.state["inbox"].append(job)
        if uncertain or legacy_running:
            bridge.reply(bridge.prompt("INPUT_RECOVERED"))
        bridge.save()
        log_event("input.recovered", component="input", count=len(jobs),
                  pending=len(bridge.state["inbox"]),
                  kind="with_uncertain" if uncertain or legacy_running else "restart")

    def defer_restart(self, job):
        bridge = self.bridge
        job.pop("restartDeferred", None)
        job.pop("authDeferred", None)
        if not any(item is job for item in bridge.state["inbox"]):
            bridge.state["inbox"].insert(0, job)
        if bridge.state.get("currentInput") is job:
            bridge.state.pop("currentInput")
        bridge.save()

    async def _prepare_attachments(self, job):
        if "preparedAttachments" in job:
            return False
        bridge = self.bridge
        job["preparedAttachments"] = await bridge.media.incoming(job.get("attachments", []))
        bridge.save()
        return True

    async def worker(self):
        bridge = self.bridge
        while True:
            if bridge.wechat_paused:
                await bridge.auth_wake.wait()
                continue
            if not can_prepare_input(bridge):
                await asyncio.sleep(0.1)
                continue
            if bridge.active_turn() or bridge.backend.blocked or not bridge.state["inbox"]:
                bridge.wake.clear()
                bridge.steer_wake.set()
                if self.awaiting_desktop():
                    try:
                        await asyncio.wait_for(bridge.wake.wait(), self.FOLLOW_POLL_SECONDS)
                    except TimeoutError:
                        await self.follow_desktop()
                else:
                    await bridge.wake.wait()
                continue
            wait = self.writer_wait_left(bridge.state["inbox"][0])
            if wait > 0:
                bridge.wake.clear()
                try:
                    await asyncio.wait_for(bridge.wake.wait(), min(wait, self.FOLLOW_POLL_SECONDS))
                except TimeoutError:
                    pass
                continue
            bridge.running = True
            bridge.stopping = False
            job = bridge.state["inbox"].pop(0)
            job["phase"] = InputPhase.PREPARING
            bridge.state["currentInput"] = job
            bridge.save()
            backend = bridge.backend
            started = time.perf_counter()
            log_event("input.prepare", component="input", phase=job["phase"],
                      mode=job.get("mode", "steer"), chars=len(job.get("text", "")),
                      count=len(job.get("attachments", [])),
                      queued=len(bridge.state["inbox"]),
                      ingress_ms=job.get("ingress_ms"),
                      request_id=job.get("requestId"))
            typing_started = False
            try:
                typing_started = await bridge.typing.start(
                    bridge.account["user"], bridge.state.get("context"))
                if bridge.wechat_paused:
                    self.defer_restart(job)
                    continue
                try:
                    await ensure_session(bridge)
                except Exception:
                    # Preparation has not submitted anything. Leave the exact job
                    # available for the connection supervisor, rather than dropping it.
                    if not bridge.stopping:
                        bridge.state["inbox"].insert(0, job)
                    bridge.state.pop("currentInput", None)
                    bridge.input_paused = True
                    bridge.save()
                    raise
                if bridge.stopping:
                    continue
                if bridge.restarting or bridge.wechat_paused:
                    self.defer_restart(job)
                    continue
                if bridge.state.get("pendingSettings"):
                    await apply_pending_settings(bridge)
                bridge.acknowledge(job)
                if await self._prepare_attachments(job):
                    log_event("input.prepared", component="input", count=len(job["preparedAttachments"]),
                              duration_ms=(time.perf_counter() - started) * 1000,
                              request_id=job.get("requestId"))
                if bridge.stopping:
                    continue
                if bridge.restarting or bridge.wechat_paused:
                    self.defer_restart(job)
                    continue
                bridge.steer_wake.set()
                thread_id = bridge.state["thread"]
                answer = await backend.run(thread_id, job["text"],
                    attachments=job["preparedAttachments"], client_id=job["clientId"],
                    allow_steer=job.get("mode", "steer") != "queue",
                    dispatch=functools.partial(bridge.dispatch_input, job, backend=backend))
                if job.get("restartDeferred") or job.get("authDeferred"):
                    if not bridge.stopping:
                        self.defer_restart(job)
                    continue
                if answer is not None:
                    bridge.last_turn_seconds = time.perf_counter() - started
                    await bridge.deliver_answer(answer, request_id=job.get("requestId"))
                    log_event("input.completed", component="input",
                              duration_ms=(time.perf_counter() - started) * 1000,
                              queued=len(bridge.state["inbox"]),
                              request_id=job.get("requestId"))
                    # Hand the writer back so the App can open this thread while
                    # the bridge is idle; the next turn resumes it or follows the App.
                    await backend.release_thread(thread_id, follow=True)
                else:
                    bridge.state.pop("currentInput", None)
                    bridge.save()
            except asyncio.CancelledError:
                raise
            except DeferTurn:
                if not bridge.stopping:
                    job.update(mode="queue", phase=InputPhase.PREPARING)
                    bridge.state["inbox"].insert(0, job)
                    bridge.reply(bridge.prompt("QUEUED_STEER"), request_id=job.get("requestId"))
                    log_event("input.deferred", component="input", status="queue",
                              phase=job["phase"], mode="queue",
                              request_id=job.get("requestId"))
                bridge.state.pop("currentInput", None)
                bridge.save()
            except Exception as error:
                conflict = (isinstance(error, CodexRpcError) and error.code == -32600
                            and "already has an active writer" in str(error.reason))
                if conflict and not bridge.stopping and self.queue_writer_conflict(bridge, job):
                    continue
                if bridge.state.get("currentInput") is job:
                    phase = job["phase"]
                    uncertain = phase == InputPhase.DISPATCHING and not (
                        isinstance(error, CodexRpcError) and error.definite_rejection)
                    bridge.record_input(job, InputPhase.UNCERTAIN if uncertain else
                                      InputPhase.ACCEPTED if phase == InputPhase.ACCEPTED else InputPhase.FAILED)
                    bridge.state.pop("currentInput", None)
                    bridge.reply(bridge.prompt("INPUT_SUBMISSION_UNCERTAIN") if uncertain else
                               bridge.prompt("TURN_STALLED") if isinstance(error, TurnStalled) else
                               bridge.prompt("INPUT_RESULT_INTERRUPTED") if phase == InputPhase.ACCEPTED else
                               bridge.prompt("SESSION_WRITER_BUSY") if conflict else
                               bridge.prompt("ATTACHMENT_INCOMING_FAILED", {"reason": attachment_reason(error)})
                               if isinstance(error, MediaError) else bridge.prompt("TASK_FAILED"),
                               request_id=job.get("requestId"))
                    log_event("input.failed", level="warning", component="input",
                              status=("uncertain" if uncertain else
                                      "accepted" if phase == InputPhase.ACCEPTED else "failed"),
                              phase=phase, mode=job.get("mode", "steer"),
                              backend=bridge.backend_name,
                              request_id=job.get("requestId"), **error_fields(error))
                else:
                    bridge.reply(bridge.prompt("BACKEND_UNREADY"),
                                 request_id=job.get("requestId"))
                    log_event("input.failed", level="warning", component="input",
                              status="backend_unavailable", phase=job.get("phase"),
                              mode=job.get("mode", "steer"), backend=bridge.backend_name,
                              request_id=job.get("requestId"), **error_fields(error))
                print(f"Agent backend error: {type(error).__name__}", file=sys.stderr, flush=True)
            finally:
                if typing_started:
                    await bridge.typing.stop()
                if bridge.stopping and bridge.state.get("currentInput") is job:
                    bridge.record_input(job, InputPhase.CANCELLED if job["phase"] == InputPhase.PREPARING
                                      else job["phase"])
                    bridge.state.pop("currentInput", None)
                    bridge.save()
                    log_event("input.cancelled", component="input", phase=job.get("phase"),
                              request_id=job.get("requestId"))
                bridge.running = False
                bridge.wake.set()

    async def steering_worker(self):
        bridge = self.bridge
        while True:
            await bridge.steer_wake.wait()
            bridge.steer_wake.clear()
            while can_steer_input(bridge):
                # Explicitly queued work must not block later live corrections.
                job = next((item for item in bridge.state["inbox"]
                            if item.get("mode", "steer") != "queue"), None)
                if job is None:
                    break
                backend = bridge.backend
                active = backend.active
                if (not active or not active["turn"] or active["done"].done()
                        or backend.pending or backend.blocked):
                    await asyncio.sleep(0.1)
                    continue
                turn_id, thread_id = active["turn"], active["thread"]
                bridge.steering = True
                typing_started = False
                try:
                    typing_started = await bridge.typing.start(
                        bridge.account["user"], bridge.state.get("context"))
                    if bridge.wechat_paused:
                        break
                    log_event("input.steer_start", component="input",
                              phase=job.get("phase", InputPhase.PREPARING),
                              mode=job.get("mode", "steer"),
                              request_id=job.get("requestId"))
                    await self._prepare_attachments(job)
                    if bridge.stopping or not any(item is job for item in bridge.state["inbox"]):
                        break
                    if backend.pending or backend.blocked:
                        continue
                    accepted = await backend.steer(thread_id, turn_id, job["text"],
                        attachments=job["preparedAttachments"], client_id=job["clientId"],
                        dispatch=functools.partial(bridge.dispatch_input, job, backend=backend))
                    if accepted:
                        bridge.state["inbox"] = [item for item in bridge.state["inbox"] if item is not job]
                        bridge.save()
                        bridge.acknowledge(job)
                        log_event("input.steer_accepted", component="input",
                                  request_id=job.get("requestId"))
                    elif job.pop("authDeferred", False):
                        bridge.save()
                        log_event("input.deferred", component="input", status="auth",
                                  phase=job.get("phase"), mode=job.get("mode", "steer"),
                                  request_id=job.get("requestId"))
                        break
                    elif not bridge.stopping:
                        job.pop("restartDeferred", None)
                        job.update(mode="queue", phase=InputPhase.PREPARING)
                        bridge.save()
                        bridge.reply(bridge.prompt("QUEUED_STEER"), request_id=job.get("requestId"))
                        log_event("input.deferred", component="input", status="queue",
                                  phase=job["phase"], mode="queue",
                                  request_id=job.get("requestId"))
                        break
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    uncertain = job.get("phase") == InputPhase.DISPATCHING
                    bridge.record_input(job, InputPhase.UNCERTAIN if uncertain else InputPhase.FAILED)
                    bridge.state["inbox"] = [item for item in bridge.state["inbox"] if item is not job]
                    bridge.save()
                    bridge.reply(bridge.prompt("UNCERTAIN_STEER") if uncertain else
                               bridge.prompt("ATTACHMENT_INCOMING_FAILED", {"reason": attachment_reason(error)})
                               if isinstance(error, MediaError) else bridge.prompt("TASK_FAILED"),
                               request_id=job.get("requestId"))
                    log_event("input.steer_failed", level="warning", component="input",
                              status="uncertain" if uncertain else "failed",
                              phase=job.get("phase"), mode=job.get("mode", "steer"),
                              request_id=job.get("requestId"), **error_fields(error))
                    print(f"追加消息失败：{type(error).__name__}", file=sys.stderr, flush=True)
                finally:
                    if typing_started:
                        await bridge.typing.stop()
                    bridge.steering = False
                    bridge.wake.set()
