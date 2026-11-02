"""Interactive WeChat rebinding shared by Linux and Windows entry points."""
import asyncio
import sys
from pathlib import Path

import service_adapter
import wechat_auth
from platform_support import data_root
from storage import Store
from wechat import login


def validate_paths(state_dir, config_path):
    expected_state = (data_root() / ".state").resolve()
    expected_config = (data_root() / "config.json").resolve()
    if Path(state_dir).expanduser().resolve() != expected_state:
        raise ValueError("rebind 只操作默认状态目录；自定义状态请手动管理登录和服务。")
    if Path(config_path).expanduser().resolve() != expected_config:
        raise ValueError("rebind 只操作默认配置路径；自定义配置请手动管理登录和服务。")


def active_work(state):
    return bool(
        state.get("currentInput")
        or state.get("running")
        or state.get("restartRequested")
    )


def _close_store(store):
    lock = getattr(store, "lock", None)
    if lock is not None:
        lock.close()


async def _acquire_locked_store(state_dir, *, wait_for_service):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 30
    while True:
        try:
            return Store(state_dir)
        except RuntimeError:
            if not wait_for_service or loop.time() >= deadline:
                raise RuntimeError("状态目录仍被另一个 codex-wechat 进程占用") from None
            await asyncio.sleep(0.2)


async def run_rebind(state_dir, config_path, *, stop_active=False, interactive=None, adapter=service_adapter,
                     login_runner=login):
    validate_paths(state_dir, config_path)
    interactive = (sys.stdin.isatty() and sys.stdout.isatty()
                   if interactive is None else interactive)
    if not interactive:
        raise ValueError("rebind 必须在交互终端扫码，不能重定向输入或输出。")

    await adapter.status()  # Refuse foreign/unavailable service management before scanning.
    preview = Store(Path(state_dir), lock=False)
    state = preview.load("bridge", {})
    if not isinstance(state, dict):
        raise RuntimeError("消息桥状态格式无效")
    if active_work(state) and not stop_active:
        raise RuntimeError(
            "当前有尚未完成的任务；确认停止桥接后使用 "
            + wechat_auth.rebind_command(stop_active=True)
        )

    previous = preview.load("account")
    # Reading live state is safe; QR waiting must neither own the lock nor stop delivery.
    result = await login_runner(preview, rebind=True, persist=False)
    service = await adapter.status()
    was_running = service.running
    store = None
    counts = {}
    # The scan can take minutes while the bridge keeps working. Re-read the
    # live state before stopping the service so work started during the scan
    # still needs the explicit --stop-active acknowledgement.
    latest = preview.load("bridge", {})
    if not isinstance(latest, dict):
        raise RuntimeError("消息桥状态格式无效")
    if active_work(latest) and not stop_active:
        raise RuntimeError(
            "当前有尚未完成的任务；确认停止桥接后使用 "
            + wechat_auth.rebind_command(stop_active=True)
        )
    try:
        if was_running:
            await adapter.stop_and_wait()
        store = await _acquire_locked_store(Path(state_dir), wait_for_service=was_running)
        state = store.load("bridge", {})
        if not isinstance(state, dict):
            raise RuntimeError("消息桥状态格式无效")
        if active_work(state) and not stop_active:
            raise RuntimeError(
                "当前有尚未完成的任务；确认停止桥接后使用 "
                + wechat_auth.rebind_command(stop_active=True)
            )
        if store.load("account") != previous:
            raise RuntimeError("扫码期间本地绑定已变化，请重新运行 rebind。")
        if result.identity_changed:
            # Persist a pause before replacing credentials; never resume a half-change.
            state["bindingPending"] = {"target": wechat_auth.identity(result.account)}
            counts.update(wechat_auth.prepare_rebind(state, previous, result.account))
            wechat_auth.require_rebind(state, pending=0)
            store.save("bridge", state)
        pending = state.get("bindingPending")
        if pending and pending.get("target") != wechat_auth.identity(result.account):
            raise RuntimeError("重新绑定身份与未完成记录不同，请显式确认要替换的身份。")
        if not result.already_connected:
            store.save("account", result.account)
        state.pop("bindingPending", None)
        if wechat_auth.mark_valid(state, result.account) or pending:
            store.save("bridge", state)
    except BaseException:
        if store is not None:
            _close_store(store)
            store = None
        if was_running:
            try:
                await adapter.start()
            except Exception as restart_error:
                print(f"重新绑定失败，且后台服务未能恢复：{restart_error}",
                      file=sys.stderr, flush=True)
        raise
    finally:
        if store is not None:
            _close_store(store)

    if result.already_connected:
        print("服务端确认已经绑定；本地凭据和聊天队列未变。", flush=True)
    elif result.identity_changed and result.previous.get("user") != result.account.get("user"):
        print(
            "微信身份已更换；旧聊天待办和发件箱已隔离，不会发送到新聊天。",
            flush=True,
        )
        print(
            f"已隔离输入 {counts.get('inputs', 0)} 条、输出 {counts.get('outbox', 0)} 条。",
            flush=True,
        )
    elif result.identity_changed:
        print("微信已重新绑定；原聊天待办和回复已保留。请在微信发一条消息以恢复发送。", flush=True)
    else:
        print("微信身份未变；原游标、待办和发件箱已保留。", flush=True)

    if was_running:
        try:
            await adapter.start()
        except Exception as error:
            raise RuntimeError(
                "微信已重新绑定，但后台服务启动失败；"
                "请运行 codex-wechat service-start 或手动启动。"
            ) from error
        print("后台服务已重新启动。", flush=True)
    else:
        print("后台服务原本未运行；完成初始化后再手动启动。", flush=True)
