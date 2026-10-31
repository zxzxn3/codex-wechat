#!/usr/bin/env python3
"""Local WeChat ↔ Codex bridge. No third-party Python dependencies."""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import uuid

from backend import CodexBackend
from storage import Store
from wechat import WeChat, login
from commands import HELP, MANAGED, ensure_session, handle, info, notice
from profiles import definitions, snapshot
import service_control

ROOT = Path(__file__).resolve().parent


class Bridge:
    def __init__(self, store, account, config, backend_factory=CodexBackend, channel=None):
        self.store, self.account, self.config = store, account, config
        self.state = store.load("bridge", {"cursor": "", "thread": None,
            "inbox": [], "outbox": [], "seen": [], "running": False})
        self.channel = channel or WeChat(account)
        self.backend_factory = backend_factory
        self.backend_configs = definitions(config)
        self.backend_name = self.state.get("backendName", "codex")
        if self.backend_name not in self.backend_configs:
            raise ValueError("保存的后端不存在于配置中：" + self.backend_name)
        self.backend = self.make_backend(self.backend_name)
        self.backend_started = False
        self.wake = asyncio.Event()
        self.send_wake = asyncio.Event()
        self.loaded = False
        self.running = False
        self.stopping = False
        self.session_lock = asyncio.Lock()
        self.model_choices = []
        self.resuming = bool(self.state["thread"])
        self.restarting = False
        self.outbox_drained = asyncio.Event()
        if not self.state["outbox"]:
            self.outbox_drained.set()

    def save(self):
        self.state["backendName"] = self.backend_name
        self.state.setdefault("backendSessions", {})[self.backend_name] = snapshot(self.state)
        self.store.save("bridge", self.state)

    def make_backend(self, name):
        return self.backend_factory(self.backend_configs[name],
            str(Path(self.config.get("cwd", ROOT / "workspace")).expanduser().resolve()), self.reply)

    async def ensure_backend(self):
        if not self.backend_started:
            try:
                await self.backend.start()
            except BaseException:
                await self.backend.close()
                raise
            self.backend_started = True

    def reply(self, text):
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
            if text.startswith("/"):
                controls.append(text)
            elif text and len(text) <= 20000:
                if len(self.state["inbox"]) >= 20:
                    self.reply("待办已达 20 条，请等待完成或使用 /stop。")
                else:
                    self.state["inbox"].append({"id": identity, "text": text})
            else:
                self.reply("第一版仅支持文字消息，每条最多 20000 字符。")
        self.state["seen"] = self.state["seen"][-2000:]
        if batch.get("get_updates_buf"):
            self.state["cursor"] = batch["get_updates_buf"]
        # Persist cursor and incoming work together, before starting any agent work.
        self.save()
        for command in controls:
            await self.control(command)
        self.wake.set()

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
        elif command == "/restart":
            try:
                if argument:
                    raise ValueError("用法：/restart")
                await self.restart_service()
            except (ValueError, RuntimeError, OSError, asyncio.TimeoutError) as error:
                self.reply(f"未重启：{error}")
        elif command == "/status":
            status = "工作中" if self.running else "空闲"
            self.reply(f"Codex：{status}；待办 {len(self.state['inbox'])} 条；"
                       f"待审批 {len(self.backend.pending)} 条。\n"
                       + info(self))
        elif command == "/stop":
            self.stopping = True
            self.state["inbox"].clear()
            self.save()
            stopped = await self.backend.stop()
            self.reply("已请求停止，并清空待办。" if stopped else "已清空待办；当前没有可中断的任务。")
        elif command in ("/approve", "/deny"):
            ok = self.backend.answer_approval(argument, "accept" if command == "/approve" else "decline")
            self.reply("已提交本次决定。" if ok else "审批编号无效或已过期。")
        else:
            self.reply("未知命令。\n" + HELP)

    async def restart_service(self):
        if self.running or self.state["inbox"] or self.session_lock.locked():
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
            if self.restarting or self.session_lock.locked():
                await asyncio.sleep(0.1)
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
                await ensure_session(self)
                if self.stopping:
                    self.state["running"] = False
                    self.save()
                    continue
                settings = notice(self)
                if settings["enabled"] and settings["text"]:
                    self.reply(settings["text"])
                answer = await self.backend.run(self.state["thread"], job["text"])
                # Persist result outbox and clear running marker in the same snapshot.
                self.state["running"] = False
                self.reply(answer)
            except asyncio.CancelledError:
                raise
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

    async def sender(self):
        while True:
            if not self.state["outbox"]:
                self.send_wake.clear()
                await self.send_wake.wait()
                continue
            entry = self.state["outbox"][0]
            try:
                await self.channel.send(entry)
            except Exception as error:
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
            if self.state["running"]:
                self.state["running"] = False
                self.state["inbox"].clear()
                self.reply("上次运行在任务完成前中断。未自动重放该任务；请检查会话后重新交代。")
            try:
                await self.ensure_backend()
                if self.state["thread"]:
                    await ensure_session(self)
            except (RuntimeError, ValueError, OSError, asyncio.TimeoutError) as error:
                print(f"后端启动或恢复失败（{type(error).__name__}）；微信控制仍可用。", file=sys.stderr)
                self.reply("后端启动或恢复失败，可用 /backend 切换后端，或处理后重新发消息。")
            tasks = [asyncio.create_task(fn()) for fn in (self.poller, self.worker, self.sender)]
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
