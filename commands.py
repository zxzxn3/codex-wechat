"""WeChat conversation controls; lists come from the current backend."""
from pathlib import Path
from profiles import switch

DEFAULT_NOTICE = {"enabled": True, "text": "::"}
HELP = ("直接发文字与 Codex 对话；忙时排队。\n"
        "/backend 查看后端；/backend codex 或 deepseek 切换\n"
        "/status 状态及会话信息\n/sessions [关键词] 会话列表\n"
        "/sessions next 下一页\n/switch 编号或ID 切换会话\n/new 新建会话\n"
        "/model 列出模型；/model 编号或名称 切换\n"
        "/effort 列出强度；/effort 档位 切换\n/cwd [路径] 查看或切换目录\n"
        "/ack 查看处理提示设置\n/ack on 或 off 开关\n/ack text 文字 自定义提示\n"
        "/send 路径 发附件（自动识别图片）\n/sendfile 路径 发文件\n/sendimage 路径 发图片\n"
        "/stop 停止并清空待办\n/restart 重启消息桥服务（空闲时）\n"
        "/approve 编号 或 /deny 编号 审批\n/help 帮助")
MANAGED = {"/backend", "/sessions", "/switch", "/new", "/model", "/models", "/effort", "/cwd", "/ack"}


def notice(bridge):
    return {**DEFAULT_NOTICE, **bridge.config.get("processingNotice", {}),
            **bridge.state.get("processingNotice", {})}


def info(bridge, heading="当前会话"):
    details = bridge.backend.info
    return (f"{heading}\n后端：{bridge.backend_name}\n名称：{details.get('name') or '未命名'}\n"
            f"会话：{bridge.state.get('thread') or '尚未创建'}\n"
            f"模型：{details.get('model') or '由 Codex 默认选择'}\n"
            f"思考强度：{details.get('effort') or 'Codex 默认（未返回具体档位）'}\n"
            f"工作目录：{details.get('cwd') or bridge.backend.cwd}")


async def activate(bridge, thread_id=None, overrides=None):
    # Persist overrides by thread, never let one session's settings leak to another.
    saved = dict(bridge.state.get("sessionOptions", {}).get(thread_id, {}))
    saved.update(overrides or {})
    if thread_id:
        thread = await bridge.backend.read_thread(thread_id)
        status = thread.get("status", {}).get("type")
        if status in ("active", "systemError"):
            raise ValueError("目标会话正在工作或等待处理，请先在原客户端处理后再切换。")
    selected = await bridge.backend.thread(thread_id, saved)
    bridge.state["thread"] = selected
    if saved:
        bridge.state.setdefault("sessionOptions", {})[selected] = saved
    bridge.loaded = True
    bridge.save()


async def ensure_session(bridge):
    await bridge.ensure_backend()
    if not bridge.loaded:
        await activate(bridge, bridge.state["thread"])
        bridge.reply(info(bridge, "已恢复会话" if bridge.resuming else "已启动会话"))
        bridge.resuming = True


async def model_entries(bridge):
    return await bridge.backend.models()


def effort_choices(model):
    return [item["reasoningEffort"] for item in model.get("supportedReasoningEfforts", [])]


async def handle(bridge, command, argument):
    if command == "/backend" and not argument:
        bridge.reply(f"当前后端：{bridge.backend_name}\n可选：" +
                     ", ".join(bridge.backend_configs) + "\n/backend 名称 切换（空闲时）")
        return
    if command == "/ack":
        settings = notice(bridge)
        if argument in ("on", "off"):
            settings["enabled"] = argument == "on"
        elif argument.startswith("text "):
            value = argument[5:].strip()
            if not value or len(value.encode()) > 1000:
                raise ValueError("提示文字不能为空，且最多 1000 字节。")
            settings["text"] = value
        elif argument:
            raise ValueError("用法：/ack、/ack on、/ack off、/ack text 自定义文字")
        if argument:
            bridge.state["processingNotice"] = settings
            bridge.save()
        bridge.reply(f"处理提示：{'开启' if settings['enabled'] else '关闭'}\n文本：{settings['text']}")
        return
    if command == "/sessions":
        cursor, search = None, argument or None
        if argument == "next":
            cursor = bridge.state.get("sessionCursor")
            search = bridge.state.get("sessionSearch")
            if not cursor:
                bridge.reply("没有下一页。请发 /sessions 重新列出。")
                return
        result = await bridge.backend.list_threads(cursor, search)
        threads = result["data"]
        bridge.state["sessionChoices"] = [thread["id"] for thread in threads]
        bridge.state["sessionCursor"] = result.get("nextCursor")
        bridge.state["sessionSearch"] = search
        bridge.save()
        lines = ["会话列表（编号以本次列表为准）："]
        for index, thread in enumerate(threads, 1):
            mark = " *" if thread["id"] == bridge.state["thread"] else ""
            title = (thread.get("name") or "未命名").replace("\n", " ")[:100]
            status = thread.get("status", {}).get("type", "unknown")
            lines.append(f"{index}. {title}{mark} [{status}]\n{thread['id']}")
        if not threads:
            lines.append("没有匹配的会话。")
        lines.append("/switch 编号 切换；/sessions next 下一页" if result.get("nextCursor")
                     else "/switch 编号或ID 切换")
        bridge.reply("\n".join(lines))
        return
    if command in ("/model", "/models") and not argument:
        models = await model_entries(bridge)
        bridge.model_choices = models
        bridge.reply("可选模型：\n" + "\n".join(
            f"{index}. {model['model']} [{', '.join(effort_choices(model)) or '无可选强度'}]"
            for index, model in enumerate(models, 1)) + "\n/model 编号或完整模型名")
        return
    if command == "/cwd" and not argument:
        bridge.reply(info(bridge))
        return
    # All state-changing session controls use one lock; incoming work waits until done.
    if bridge.running or bridge.state["inbox"] or bridge.session_lock.locked():
        raise ValueError("当前有任务或待办；请先 /stop，等待结束后再切换设置。")
    async with bridge.session_lock:
        if command == "/backend":
            await switch(bridge, argument)
            bridge.reply(info(bridge, "已选择后端"))
            return
        if command == "/new":
            if argument:
                raise ValueError("用法：/new")
            await activate(bridge)
            bridge.resuming = True
            bridge.reply(info(bridge, "已新建会话"))
            return
        if command == "/switch":
            if not argument:
                raise ValueError("用法：/switch 列表编号或完整会话ID")
            target = argument
            if argument.isdecimal():
                choices = bridge.state.get("sessionChoices", [])
                index = int(argument) - 1
                if not 0 <= index < len(choices):
                    raise ValueError("编号无效，请先 /sessions。")
                target = choices[index]
            await activate(bridge, target)
            bridge.reply(info(bridge, "已切换会话"))
            return
        await ensure_session(bridge)
        overrides = {}
        if command in ("/model", "/models", "/effort"):
            models = (bridge.model_choices if argument.isdecimal() and bridge.model_choices
                      and command != "/effort" else await model_entries(bridge))
            if command == "/effort":
                current = next((m for m in models if m["model"] == bridge.backend.info.get("model")), None)
                if current is None:
                    raise ValueError("当前模型不在返回的模型清单中，无法核对强度。")
                choices = effort_choices(current)
                if not argument:
                    bridge.reply(f"当前强度：{bridge.backend.info.get('effort') or '默认'}\n"
                                 f"可选：{', '.join(choices) or '无'}\n/effort 档位")
                    return
                if argument not in choices:
                    raise ValueError("此模型的可选强度：" + ", ".join(choices))
                overrides["effort"] = argument
            else:
                selected = None
                if argument.isdecimal():
                    index = int(argument) - 1
                    if 0 <= index < len(models):
                        selected = models[index]
                else:
                    selected = next((m for m in models if m["model"] == argument), None)
                if selected is None:
                    raise ValueError("模型不在返回的清单中，请先 /model。")
                overrides["model"] = selected["model"]
                # Model-specific defaults prevent carrying an unsupported effort to another model.
                overrides["effort"] = selected.get("defaultReasoningEffort")
        elif command == "/cwd":
            base = Path(bridge.backend.info.get("cwd") or bridge.backend.cwd)
            path = Path(argument).expanduser()
            if not path.is_absolute():
                path = base / path
            try:
                path = path.resolve(strict=True)
            except OSError:
                raise ValueError("目录不存在或无法访问。") from None
            if not path.is_dir():
                raise ValueError("路径不是目录。")
            overrides["cwd"] = str(path)
        await activate(bridge, bridge.state["thread"], overrides)
        bridge.reply(info(bridge, "设置已更新"))
