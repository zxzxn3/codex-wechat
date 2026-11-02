"""Input preparation, steering and recovery transitions.

The bridge is the shared runtime context and sole persistence owner. This component
never copies backend selection or persistent queues across an await.
"""
from dataclasses import asdict
import asyncio
import sys
import time
import uuid
from backend import CodexRpcError, DeferTurn
from commands import ensure_session
from records import InputPhase
from media import MediaError
from errors import attachment_reason
from diagnostics import error_fields, log_event
from prompts import (
    ATTACHMENT_INCOMING_FAILED,
    QUEUED_STEER,
    TASK_FAILED,
    UNCERTAIN_STEER,
    fill,
)

class InputDelivery:
    def __init__(self, bridge):
        self.bridge = bridge

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
            bridge.reply("上次有任务已提交或提交结果未确认，未自动重发。未提交的待办已保留。")
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

    async def worker(self):
        bridge = self.bridge
        while True:
            if bridge.wechat_paused:
                await bridge.auth_wake.wait()
                continue
            if (bridge.restarting or bridge.steering or bridge.session_lock.locked() or bridge.input_paused
                    or bridge.state.get("wechatContextPending")):
                await asyncio.sleep(0.1)
                continue
            if bridge.active_turn() or bridge.backend.blocked or not bridge.state["inbox"]:
                bridge.wake.clear()
                bridge.steer_wake.set()
                await bridge.wake.wait()
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
            try:
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
                bridge.acknowledge(job)
                if "preparedAttachments" not in job:
                    job["preparedAttachments"] = await bridge.media.incoming(job.get("attachments", []))
                    bridge.save()
                    log_event("input.prepared", component="input", count=len(job["preparedAttachments"]),
                              duration_ms=(time.perf_counter() - started) * 1000,
                              request_id=job.get("requestId"))
                if bridge.stopping:
                    continue
                if bridge.restarting or bridge.wechat_paused:
                    self.defer_restart(job)
                    continue
                bridge.steer_wake.set()
                answer = await backend.run(bridge.state["thread"], job["text"],
                    attachments=job["preparedAttachments"], client_id=job["clientId"],
                    allow_steer=job.get("mode", "steer") != "queue",
                    dispatch=lambda phase, turn: bridge.dispatch_input(job, phase, turn, backend))
                if job.get("restartDeferred") or job.get("authDeferred"):
                    if not bridge.stopping:
                        self.defer_restart(job)
                    continue
                if answer is not None:
                    await bridge.deliver_answer(answer)
                    log_event("input.completed", component="input",
                              duration_ms=(time.perf_counter() - started) * 1000,
                              queued=len(bridge.state["inbox"]),
                              request_id=job.get("requestId"))
                else:
                    bridge.state.pop("currentInput", None)
                    bridge.save()
            except asyncio.CancelledError:
                raise
            except DeferTurn:
                if not bridge.stopping:
                    job.update(mode="queue", phase=InputPhase.PREPARING)
                    bridge.state["inbox"].insert(0, job)
                    bridge.reply(QUEUED_STEER)
                    log_event("input.deferred", component="input", status="queue",
                              phase=job["phase"], mode="queue",
                              request_id=job.get("requestId"))
                bridge.state.pop("currentInput", None)
                bridge.save()
            except Exception as error:
                if bridge.state.get("currentInput") is job:
                    phase = job["phase"]
                    uncertain = phase == InputPhase.DISPATCHING and not (
                        isinstance(error, CodexRpcError) and error.definite_rejection)
                    bridge.record_input(job, InputPhase.UNCERTAIN if uncertain else
                                      InputPhase.ACCEPTED if phase == InputPhase.ACCEPTED else InputPhase.FAILED)
                    bridge.state.pop("currentInput", None)
                    bridge.reply("消息提交结果未确认，未自动重发。" if uncertain else
                               "任务已提交，但结果接收中断，未重新执行。" if phase == InputPhase.ACCEPTED else
                               fill(ATTACHMENT_INCOMING_FAILED, {"原因": attachment_reason(error)})
                               if isinstance(error, MediaError) else TASK_FAILED)
                    log_event("input.failed", level="warning", component="input",
                              status=("uncertain" if uncertain else
                                      "accepted" if phase == InputPhase.ACCEPTED else "failed"),
                              phase=phase, mode=job.get("mode", "steer"),
                              backend=bridge.backend_name,
                              request_id=job.get("requestId"), **error_fields(error))
                else:
                    bridge.reply("后端尚未就绪，未提交的消息已保留；可用 `/stop` 清空。")
                    log_event("input.failed", level="warning", component="input",
                              status="backend_unavailable", phase=job.get("phase"),
                              mode=job.get("mode", "steer"), backend=bridge.backend_name,
                              request_id=job.get("requestId"), **error_fields(error))
                print(f"Agent backend error: {type(error).__name__}", file=sys.stderr, flush=True)
            finally:
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
            while ((bridge.running or bridge.active_turn()) and bridge.state["inbox"]
                   and not bridge.stopping and not bridge.restarting and not bridge.wechat_paused
                   and not bridge.state.get("wechatContextPending")):
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
                try:
                    log_event("input.steer_start", component="input",
                              phase=job.get("phase", InputPhase.PREPARING),
                              mode=job.get("mode", "steer"),
                              request_id=job.get("requestId"))
                    if "preparedAttachments" not in job:
                        job["preparedAttachments"] = await bridge.media.incoming(job.get("attachments", []))
                        bridge.save()
                    if bridge.stopping or not any(item is job for item in bridge.state["inbox"]):
                        break
                    if backend.pending or backend.blocked:
                        continue
                    accepted = await backend.steer(thread_id, turn_id, job["text"],
                        attachments=job["preparedAttachments"], client_id=job["clientId"],
                        dispatch=lambda phase, turn: bridge.dispatch_input(job, phase, turn, backend))
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
                        bridge.reply(QUEUED_STEER)
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
                    bridge.reply(UNCERTAIN_STEER if uncertain else
                               fill(ATTACHMENT_INCOMING_FAILED, {"原因": attachment_reason(error)})
                               if isinstance(error, MediaError) else TASK_FAILED)
                    log_event("input.steer_failed", level="warning", component="input",
                              status="uncertain" if uncertain else "failed",
                              phase=job.get("phase"), mode=job.get("mode", "steer"),
                              request_id=job.get("requestId"), **error_fields(error))
                    print(f"追加消息失败：{type(error).__name__}", file=sys.stderr, flush=True)
                finally:
                    bridge.steering = False
                    bridge.wake.set()
