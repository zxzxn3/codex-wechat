#!/usr/bin/env python3
"""Local WeChat ↔ Codex bridge. Media transport uses cryptography."""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import uuid

from backend import CodexBackend, DeferTurn
from storage import Store
from wechat import WeChat, login
from commands import HELP, MANAGED, ensure_session, handle, info, notice
from profiles import definitions, snapshot
import service_control
from media import Media, MediaError, MAX_ATTACHMENTS, split_answer

ROOT = Path(__file__).resolve().parent
SEND_ATTEMPTS = 3


class Bridge:
    def __init__(self, store, account, config, backend_factory=CodexBackend, channel=None):
        self.store, self.account, self.config = store, account, config
        self.state = store.load("bridge", {"cursor": "", "thread": None,
            "inbox": [], "outbox": [], "seen": [], "running": False})
        self.channel = channel or WeChat(account)
        self.media = Media(store, self.channel, config.get("media"))
        self.backend_factory = backend_factory
        self.backend_configs = definitions(config)
        self.backend_name = self.state.get("backendName", "codex")
        if self.backend_name not in self.backend_configs:
            raise ValueError("保存的后端不存在于配置中：" + self.backend_name)
        self.backend = self.make_backend(self.backend_name)
        self.backend_started = False
        self.wake = asyncio.Event()
        self.steer_wake = asyncio.Event()
        self.external_results = asyncio.Queue()
        self.send_wake = asyncio.Event()
        self.loaded = False
        self.running = False
        self.steering = False
        self.input_mode = config.get("inputMode", "steer")
        if self.input_mode not in ("steer", "queue"):
            raise ValueError("inputMode 必须是 steer 或 queue")
        self.stopping = False
        self.session_lock = asyncio.Lock()
        self.model_choices = []
        self.resuming = bool(self.state["thread"])
        self.phase = "空闲"
        self.restarting = False
        self.outbox_drained = asyncio.Event()
        if not self.state["outbox"]:
            self.outbox_drained.set()

    def save(self):
        self.state["backendName"] = self.backend_name
        self.state.setdefault("backendSessions", {})[self.backend_name] = snapshot(self.state)
        self.store.save("bridge", self.state)

    def set_phase(self, phase):
        self.phase = phase
        self.state["phase"] = phase
        self.save()
        print(f"任务阶段：{phase}", file=sys.stderr, flush=True)

    def make_backend(self, name):
        backend = self.backend_factory(self.backend_configs[name],
            str(Path(self.config.get("cwd", ROOT / "workspace")).expanduser().resolve()), self.reply)
        backend.on_activity = self.backend_activity
        backend.on_external_result = lambda thread, text, cwd: self.external_results.put_nowait(
            {"thread": thread, "text": text, "cwd": cwd})
        return backend

    def backend_activity(self):
        # Notifications never start work or change the selected session.
        if hasattr(self, "wake"):
            self.wake.set()
            self.steer_wake.set()

    def active_turn(self):
        active = self.backend.active
        return active if active and not active["done"].done() else None

    async def ensure_backend(self):
        if not self.backend_started:
            try:
                await self.backend.start()
            except BaseException:
                await self.backend.close()
                raise
            self.backend_started = True

    def reply(self, text, *, persist=True):
        context = self.state.get("context")
        if not context:
            return
        # Chunk on Unicode codepoints without splitting UTF-8 sequences.
        chunks, part, size = [], "", 0
        for char in text:
            width = len(char.encode())
            if size + width > 3000:
                chunks.append(part)
                part, size = "", 0
            part += char
            size += width
        if part:
            chunks.append(part)
        for chunk in chunks:
            self.state["outbox"].append({"id": str(uuid.uuid4()), "user": self.account["user"],
                                        "context": context, "text": chunk})
        if chunks:
            self.outbox_drained.clear()
        if persist:
            self.save()
        self.send_wake.set()

    def asset_entry(self, asset):
        return {"id": str(uuid.uuid4()), "user": self.account["user"],
                "context": self.state["context"], "asset": asset}

    async def queue_file(self, path, kind="auto"):
        cwd = self.backend.info.get("cwd") or self.backend.cwd
        asset = await asyncio.to_thread(self.media.snapshot, path, cwd, kind)
        self.state["outbox"].append(self.asset_entry(asset))
        self.outbox_drained.clear()
        self.save()
        self.send_wake.set()

    async def deliver_answer(self, answer, *, finish_running=True, cwd=None):
        text, specs = split_answer(answer)
        entries, errors = [], []
        if len(specs) > MAX_ATTACHMENTS:
            errors.append(f"最多回传 {MAX_ATTACHMENTS} 个附件，超出的已跳过。")
        for spec in specs[:MAX_ATTACHMENTS]:
            try:
                asset = await asyncio.to_thread(self.media.snapshot, spec["path"],
                    cwd or self.backend.info.get("cwd") or self.backend.cwd,
                    spec.get("kind", "auto"), automatic=True)
                entries.append(self.asset_entry(asset))
            except (MediaError, ValueError) as error:
                errors.append(f"附件未加入发送队列：{error}")
        # Commit the final text, frozen files and completion marker in one snapshot.
        if finish_running:
            self.state["running"] = False
        if text or errors:
            self.reply("\n\n".join(part for part in (text, "\n".join(errors)) if part), persist=False)
        self.state["outbox"].extend(entries)
        if entries:
            self.outbox_drained.clear()
        self.save()
        self.send_wake.set()

    async def receive(self, batch):
        controls = []
        for msg in batch.get("msgs", []):
            if (msg.get("from_user_id") != self.account["user"] or msg.get("group_id")
                    or msg.get("message_type") != 1):
                continue
            # Use a stable fallback when the backend omits message_id.
            identity = msg.get("message_id") or msg.get("client_id") or hashlib.sha256(
                json.dumps(msg, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            identity = str(identity)
            if identity in self.state["seen"]:
                continue
            if not msg.get("context_token"):
                # Cannot safely reply without a context token; don't execute.
                continue
            self.state["context"] = msg["context_token"]
            self.state["seen"].append(identity)
            text = "\n".join(item.get("text_item", {}).get("text", "")
                             for item in msg.get("item_list", []) if item.get("type") == 1).strip()
            attachments = [item for item in msg.get("item_list", []) if item.get("type") in (2, 4)]
            if len(attachments) > MAX_ATTACHMENTS:
                self.reply(f"每条消息最多 {MAX_ATTACHMENTS} 个附件。")
            elif text.startswith("/") and not attachments:
                controls.append(text)
            elif (text or attachments) and len(text) <= 20000:
                if len(self.state["inbox"]) >= 20:
                    self.reply("待办已达 20 条，请等待完成或使用 /stop。")
                else:
                    self.state["inbox"].append({"id": identity, "text": text or "请查看我发送的附件。",
                                               "attachments": attachments, "mode": self.input_mode})
            else:
                self.reply("支持文字、图片和文件；文字每条最多 20000 字符。")
        self.state["seen"] = self.state["seen"][-2000:]
        if batch.get("get_updates_buf"):
            self.state["cursor"] = batch["get_updates_buf"]
        # Persist cursor and incoming work together, before starting any agent work.
        self.save()
        for command in controls:
            await self.control(command)
        self.wake.set()
        self.steer_wake.set()

    async def control(self, text):
        command, _, argument = text.partition(" ")
        argument = argument.strip()
        if command in MANAGED:
            try:
                if command not in ("/backend", "/ack"):
                    await self.ensure_backend()
                await handle(self, command, argument)
            except (ValueError, RuntimeError, OSError, asyncio.TimeoutError) as error:
                self.reply(f"操作未完成：{error}")
        elif command == "/help":
            self.reply(HELP)
        elif command in ("/send", "/sendfile", "/sendimage"):
            try:
                if not argument:
                    raise MediaError("用法：/send 路径；/sendfile 强制文件；/sendimage 强制图片。")
                if len(argument) >= 2 and argument[0] == argument[-1] and argument[0] in ("'", '"'):
                    argument = argument[1:-1]
                await self.queue_file(argument, {"/sendfile": "file", "/sendimage": "image"}.get(command, "auto"))
                self.reply("已加入附件发送队列。")
            except (MediaError, ValueError, OSError) as error:
                self.reply(f"附件未发送：{error}")
        elif command == "/restart":
            try:
                if argument:
                    raise ValueError("用法：/restart")
                await self.restart_service()
            except (ValueError, RuntimeError, OSError, asyncio.TimeoutError) as error:
                self.reply(f"未重启：{error}")
        elif command == "/status":
            status = "工作中" if self.running or self.steering or self.active_turn() else "空闲"
            self.reply(f"Codex：{status}；待办 {len(self.state['inbox'])} 条；"
                       f"待审批 {len(self.backend.pending)} 条。\n"
                       f"失败发送记录：{len(self.state.get('failedOutbox', []))} 条\n"
                       f"阶段：{self.phase}\n"
                       f"收信模式：{self.input_mode}；正在追加：{'是' if self.steering else '否'}\n"
                       + info(self, detailed=True))
        elif command == "/stop":
            self.stopping = True
            self.state["inbox"].clear()
            self.save()
            try:
                stopped = await self.backend.stop()
                self.reply("已请求停止，并清空待办。" if stopped else
                           ("已标记停止并清空待办；当前准备或附件处理结束后将停止。" if self.running else
                            "已清空待办；当前没有可中断的任务。"))
            except (RuntimeError, OSError, asyncio.TimeoutError):
                self.reply("待办已清空，但停止当前轮次失败或未确认；请在桌面检查，勿当作已停止。")
        elif command in ("/approve", "/deny"):
            try:
                ok = await self.backend.answer_approval(argument, "accept" if command == "/approve" else "decline")
                self.reply("已提交本次决定。" if ok else "审批编号无效或已过期。")
            except (RuntimeError, OSError):
                self.reply("审批决定发送失败；请在桌面处理或刷新状态。")
        else:
            self.reply("未知命令。\n" + HELP)

    async def restart_service(self):
        if self.running or self.steering or self.active_turn() or self.state["inbox"] or self.session_lock.locked():
            raise ValueError("当前有任务或待办，请先 /stop 并等待任务结束，再发 /restart。")
        async with self.session_lock:
            await service_control.require_service_owner()
            self.restarting = True
            try:
                self.reply("准备重启消息桥服务，稍后恢复当前后端和会话。")
                try:
                    await asyncio.wait_for(self.outbox_drained.wait(), 20)
                except asyncio.TimeoutError:
                    raise RuntimeError("确认消息未能及时发送，本次重启已取消。") from None
                self.state["restartRequested"] = True
                self.save()
                await service_control.restart()
                # Remain quiescent until systemd terminates us; don't accept more work.
                await asyncio.Event().wait()
            finally:
                if not asyncio.current_task().cancelling():
                    self.state.pop("restartRequested", None)
                    self.restarting = False
                    self.save()

    async def worker(self):
        while True:
            if self.restarting or self.steering or self.session_lock.locked():
                await asyncio.sleep(0.1)
                continue
            if self.active_turn() or self.backend.blocked:
                self.wake.clear()
                self.steer_wake.set()
                await self.wake.wait()
                continue
            if not self.state["inbox"]:
                self.wake.clear()
                await self.wake.wait()
                continue
            self.running = True
            self.stopping = False
            job = self.state["inbox"].pop(0)
            self.state["running"] = True
            self.save()
            try:
                self.set_phase("准备会话")
                await ensure_session(self)
                if self.stopping:
                    self.state["running"] = False
                    self.save()
                    continue
                settings = notice(self)
                if settings["enabled"] and settings["text"]:
                    self.reply(settings["text"])
                self.set_phase("下载和解密附件" if job.get("attachments") else "准备模型输入")
                attachments = (job["preparedAttachments"] if "preparedAttachments" in job else
                               await self.media.incoming(job.get("attachments", [])))
                if self.stopping:
                    self.state["running"] = False
                    self.save()
                    continue
                self.set_phase("Agent 处理（含模型能力查询）")
                self.steer_wake.set()
                answer = await self.backend.run(self.state["thread"], job["text"], attachments=attachments)
                if answer is not None:
                    self.set_phase("准备回传结果")
                    await self.deliver_answer(answer)
                else:
                    self.state["running"] = False
                    self.save()
            except asyncio.CancelledError:
                raise
            except DeferTurn:
                job["mode"] = "queue"
                job["preparedAttachments"] = attachments
                self.state["inbox"].insert(0, job)
                self.state["running"] = False
                self.save()
            except MediaError as error:
                self.state["running"] = False
                self.reply(f"附件处理失败，本条未交给 Agent：{error}")
            except Exception as error:
                self.state["running"] = False
                self.state["inbox"].clear()
                self.reply(f"任务未完成（{type(error).__name__}）。已暂停待办，未自动重放；"
                           "可通过 /status 查看会话，或 /backend 切换后端。")
                print(f"Agent backend error: {type(error).__name__}", file=sys.stderr, flush=True)
                # Keep the WeChat controls available so the user can select a healthy backend.
                await self.backend.close()
                self.backend = self.make_backend(self.backend_name)
                self.backend_started = False
                self.loaded = False
            finally:
                self.running = False
                self.set_phase("处理补充消息" if self.steering else "空闲")

    async def steering_worker(self):
        while True:
            await self.steer_wake.wait()
            self.steer_wake.clear()
            while (self.running or self.active_turn()) and self.state["inbox"] and not self.stopping:
                job = self.state["inbox"][0]
                if job.get("mode", self.input_mode) == "queue":
                    break
                backend = self.backend
                active = backend.active
                # Preparation has no turn id yet; approvals must be handled before steering.
                if (not active or not active["turn"] or active["done"].done()
                        or backend.pending or backend.blocked):
                    await asyncio.sleep(0.1)
                    continue
                turn_id, thread_id = active["turn"], active["thread"]
                self.steering = True
                try:
                    if "preparedAttachments" not in job:
                        job["preparedAttachments"] = await self.media.incoming(job.get("attachments", []))
                        self.save()
                    if self.stopping or not any(item is job for item in self.state["inbox"]):
                        break
                    if backend.pending or backend.blocked:
                        continue
                    # Persist the uncertain-delivery boundary before asking the backend.
                    job["dispatching"] = True
                    self.save()
                    accepted = await backend.steer(thread_id, turn_id, job["text"],
                                                    attachments=job["preparedAttachments"])
                    if accepted:
                        self.state["inbox"] = [item for item in self.state["inbox"] if item is not job]
                        self.save()
                        settings = notice(self)
                        if settings["enabled"] and settings["text"]:
                            self.reply(settings["text"])
                    else:
                        job.pop("dispatching", None)
                        job["mode"] = "queue"
                        self.save()
                        break
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    self.state["inbox"] = [item for item in self.state["inbox"] if item is not job]
                    self.save()
                    if isinstance(error, MediaError):
                        self.reply(f"补充消息的附件处理失败，未投递给 Agent：{error}")
                    else:
                        self.reply("补充消息未能确认投递，未自动重发。请先用 /status 确认进度。")
                    print(f"追加消息失败：{type(error).__name__}", file=sys.stderr, flush=True)
                finally:
                    self.steering = False
                    if not self.running:
                        self.set_phase("空闲")
                    self.wake.set()

    async def external_result_worker(self):
        while True:
            result = await self.external_results.get()
            try:
                if result["thread"] != self.state["thread"]:
                    self.reply(f"会话 {result['thread']} 的后续结果：")
                await self.deliver_answer(result["text"], finish_running=False, cwd=result["cwd"])
            finally:
                self.external_results.task_done()

    async def sender(self):
        while True:
            if not self.state["outbox"]:
                self.send_wake.clear()
                await self.send_wake.wait()
                continue
            entry = self.state["outbox"][0]
            try:
                if "asset" in entry and "item" not in entry:
                    entry["item"] = await self.media.upload(entry)
                    self.save()
                await self.channel.send(entry)
            except Exception as error:
                entry["attempts"] = entry.get("attempts", 0) + 1
                if entry["attempts"] >= SEND_ATTEMPTS:
                    self.state["outbox"].pop(0)
                    self.state["failedOutbox"] = (self.state.get("failedOutbox", []) + [entry])[-20:]
                    self.save()
                    if "asset" in entry:
                        self.reply(f"附件发送失败：{entry['asset']['name']}。已尝试 3 次，请稍后用 /send 重试。")
                    else:
                        print("文字发送重试耗尽，已保存失败记录并继续后续回复。", file=sys.stderr, flush=True)
                    if not self.state["outbox"]:
                        self.outbox_drained.set()
                    continue
                self.save()
                print(f"发送失败（{type(error).__name__}），稍后重试；回复已保存。", file=sys.stderr)
                await asyncio.sleep(5)
                continue
            self.state["outbox"].pop(0)
            self.save()
            if not self.state["outbox"]:
                self.outbox_drained.set()

    async def poller(self):
        delay = 2
        while True:
            try:
                batch = await self.channel.poll(self.state["cursor"])
                await self.receive(batch)
                delay = 2
            except Exception as error:
                print(f"接收失败：{error}", file=sys.stderr, flush=True)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)

    async def run(self):
        tasks = []
        try:
            if self.state.pop("restartRequested", False):
                self.reply("消息桥进程已重新启动。")
            uncertain = [job for job in self.state["inbox"] if job.get("dispatching")]
            if uncertain:
                self.state["inbox"] = [job for job in self.state["inbox"] if not job.get("dispatching")]
                self.reply("上次有补充消息的投递结果未确认，未自动重放。请检查当前进度。")
            if self.state["running"]:
                self.state["running"] = False
                self.state["inbox"].clear()
                self.reply("上次消息桥在等待结果时退出，未自动重放；共享后台可能仍在执行，请查看当前状态。")
            stage = "启动后端连接"
            try:
                await self.ensure_backend()
                if self.state["thread"]:
                    stage = "恢复会话"
                    await ensure_session(self)
            except (RuntimeError, ValueError, OSError, asyncio.TimeoutError) as error:
                detail = str(error) if isinstance(error, (RuntimeError, ValueError)) else type(error).__name__
                print(f"{stage}失败：{detail}；微信控制仍可用。", file=sys.stderr)
                self.reply(f"{stage}失败：{detail}\n可用 /backend 重试或切换后端。")
            tasks = [asyncio.create_task(fn()) for fn in
                     (self.poller, self.worker, self.steering_worker, self.external_result_worker, self.sender)]
            print("微信消息桥已启动；Ctrl+C 停止。", flush=True)
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self.backend.close()


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("login", "start", "status"))
    parser.add_argument("--state-dir", type=Path, default=ROOT / ".state")
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    args = parser.parse_args()
    os.umask(0o077)
    store = Store(args.state_dir, lock=args.action != "status")
    if args.action == "login":
        await login(store)
    elif args.action == "status":
        state = store.load("bridge", {})
        print(json.dumps({"bound": bool(store.load("account")), "thread": state.get("thread"),
                          "backend": state.get("backendName", "codex"),
                          "unfinished_at_last_save": state.get("running", False),
                          "queued": len(state.get("inbox", [])),
                          "unsent": len(state.get("outbox", []))}, ensure_ascii=False, indent=2))
    else:
        account = store.load("account")
        if not account:
            raise RuntimeError("尚未绑定微信，请先运行 login")
        config = json.loads(args.config.read_text()) if args.config.exists() else {}
        cwd = Path(config.get("cwd", ROOT / "workspace")).expanduser()
        cwd.mkdir(parents=True, exist_ok=True)
        bridge = Bridge(store, account, config)
        loop = asyncio.get_running_loop()
        main_task = asyncio.current_task()
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(signum, main_task.cancel)
        await bridge.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
