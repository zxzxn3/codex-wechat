"""App-owned session adoption; fake local router only, never real Codex."""
import asyncio
import copy
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import uuid
from unittest.mock import AsyncMock, Mock, patch

import app_ipc
from app_ipc import (AppSession, VERSIONS, app_inputs, apply_patches, ipc_path,
                     turns)
from backend import CodexBackend
from codex_transport import CodexRpcError


def conversation():
    return {"id": "thread", "hostId": "local", "title": "fixture", "cwd": "/tmp",
        "latestModel": "model", "latestReasoningEffort": "high",
        "threadRuntimeStatus": {"type": "idle"}, "requests": [],
        "turns": [{"turnId": "old", "status": "completed", "items": [
            {"type": "agentMessage", "id": "old-item", "phase": "final_answer", "text": "old"}]}]}


class StateTests(unittest.TestCase):
    def test_app_text_defaults_without_mutating_native_input(self):
        native = [{"type": "text", "text": "你好"},
                  {"type": "localImage", "path": "/tmp/fixture.png"},
                  {"type": "text", "text": "引用", "text_elements": [{"placeholder": "fixture"}]}]
        result = app_inputs(native)
        self.assertEqual(result[0]["text_elements"], [])
        self.assertNotIn("text_elements", native[0])
        self.assertEqual(result[1:], native[1:])

    def test_patch_operations_are_atomic_and_keep_unicode(self):
        state = {"items": [{"text": "旧"}]}
        updated = apply_patches(state, [
            {"op": "replace", "path": ["items", 0, "text"], "value": "新"},
            {"op": "add", "path": ["items", 1], "value": {}},
            {"op": "remove", "path": ["items", 1]}])
        self.assertEqual(updated, {"items": [{"text": "新"}]})
        self.assertEqual(state["items"][0]["text"], "旧")
        with self.assertRaises(ValueError):
            apply_patches(state, [{"op": "remove", "path": ["items", -1]}])

    def test_canonical_turn_order_uses_islands(self):
        state = {"turnHistory": {"kind": "canonical", "history": {
            "entitiesByKey": {"b": {"turnId": "two"}, "a": {"turnId": "one"}},
            "islands": [{"entries": [{"value": "a"}, {"value": "b"}]}]}}}
        self.assertEqual([t["turnId"] for t in turns(state)], ["one", "two"])

    def test_home_is_not_guessed_for_custom_launcher_or_explicit_websocket(self):
        with patch.object(app_ipc, "WINDOWS", False):
            self.assertIsNone(ipc_path({"command": ["codex-deepseek", "app-server"]}))
            self.assertEqual(ipc_path({"command": ["custom", "app-server"],
                "env": {"CODEX_HOME": "/tmp/home"}}), Path("/tmp/home/ipc/ipc.sock"))
            self.assertIsNone(ipc_path({"websocketUrl": "ws://127.0.0.1:1234"}))
            self.assertEqual(ipc_path({"websocketUrl": "ws://127.0.0.1:1234",
                "env": {"CODEX_HOME": "/tmp/home"}}), Path("/tmp/home/ipc/ipc.sock"))


@unittest.skipIf(os.name == "nt", "Linux Unix-socket App IPC")
class RouterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "ipc.sock"
        self.state = conversation()
        self.messages = []
        self.clients = set()
        self.events = []
        self.received_requests = []
        self.failed = Mock()
        self.nested_response = False
        self.server = await asyncio.start_unix_server(self.handle, path=self.path)
        self.session = AppSession(self.path, "thread", on_event=self.events.append,
            on_request=AsyncMock(side_effect=self.received_requests.append), on_disconnect=self.failed)

    async def asyncTearDown(self):
        await self.session.close()
        self.server.close()
        for writer in self.clients:
            writer.close()
        await self.server.wait_closed()
        self.temp.cleanup()

    async def frame(self, writer, message):
        b = json.dumps(message, ensure_ascii=False).encode()
        writer.write(struct.pack("<I", len(b)) + b)
        await writer.drain()

    async def snapshot(self, writer, *, owner="owner", revision=1):
        await self.frame(writer, {"type": "broadcast", "method": "thread-stream-state-changed",
            "version": 11, "sourceClientId": owner, "targetClientIds": ["client"],
            "params": {"hostId": "local", "conversationId": "thread", "change": {
                "type": "snapshot", "revision": revision, "conversationState": self.state}}})

    async def handle(self, reader, writer):
        self.clients.add(writer)
        try:
            while True:
                n = struct.unpack("<I", await reader.readexactly(4))[0]
                m = json.loads(await reader.readexactly(n))
                self.messages.append(m)
                if m["type"] == "broadcast":
                    await self.snapshot(writer)
                    continue
                method = m["method"]
                self.assertEqual(m["version"], VERSIONS[method])
                if method == "initialize":
                    result, owner = {"clientId": "client"}, "client"
                elif method == "thread-owner-discovery":
                    result, owner = {"supportsUntrustedAppInput": True}, "owner"
                else:
                    owner = "owner"
                    # Real App IPC responses keep `method` at the response top
                    # level; `result` contains only the method result.
                    result = {"result": {"turn": {"id": "new"}}} if method == "thread-follower-start-turn" else {"ok": True}
                    if self.nested_response:
                        result = {"method": method, "result": result}
                await self.frame(writer, {"type": "response", "requestId": m["requestId"],
                    "resultType": "success", "method": method, "handledByClientId": owner, "result": result})
        except (asyncio.IncompleteReadError, ConnectionError):
            pass

    async def test_attach_has_no_history_replay_and_new_output_uses_native_events(self):
        await self.session.attach()
        self.session.activate()
        self.assertFalse(any(e["method"] == "item/completed" for e in self.events))
        result = await self.session.rpc("turn/start", {"threadId": "thread", "input": []})
        self.assertEqual(result["turn"]["id"], "new")
        self.state["turns"].append({"turnId": "new", "status": "completed", "items": [
            {"type": "agentMessage", "id": "new-item", "phase": "final_answer", "text": "回复"}]})
        await self.session.broadcast({"type": "broadcast", "version": 11,
            "sourceClientId": "owner", "method": "thread-stream-state-changed", "params": {
                "hostId": "local", "conversationId": "thread", "change": {
                    "type": "snapshot", "revision": 2, "conversationState": copy.deepcopy(self.state)}}})
        complete = [e for e in self.events if e["method"] == "item/completed"]
        self.assertEqual([e["params"]["item"]["text"] for e in complete], ["回复"])
        self.assertEqual(self.events[-1]["method"], "turn/completed")

    async def test_nested_response_wrapper_remains_supported(self):
        self.nested_response = True
        await self.session.attach()
        result = await self.session.rpc("turn/start", {"threadId": "thread", "input": []})
        self.assertEqual(result, {"turn": {"id": "new"}})
        self.assertEqual(self.session.requests, {})

    async def test_wrong_owner_or_thread_does_not_replace_state(self):
        await self.session.attach()
        before = copy.deepcopy(self.session.state)
        await self.session.broadcast({"type": "broadcast", "version": 11, "sourceClientId": "other",
            "method": "thread-stream-state-changed", "params": {"hostId": "local", "conversationId": "thread",
                "change": {"type": "snapshot", "revision": 2, "conversationState": {"id": "other"}}}})
        self.assertEqual(self.session.state, before)

    async def test_steer_refuses_stale_turn_without_sending(self):
        await self.session.attach()
        before = len(self.messages)
        with self.assertRaises(CodexRpcError) as error:
            await self.session.rpc("turn/steer", {"threadId": "thread", "expectedTurnId": "ended", "input": []})
        self.assertTrue(error.exception.definite_rejection)
        self.assertEqual(len(self.messages), before)

    async def test_start_and_steer_always_supply_app_text_elements(self):
        await self.session.attach()
        native = {"threadId": "thread", "input": [{"type": "text", "text": "fixture"}]}
        await self.session.rpc("turn/start", native)
        sent = self.messages[-1]["params"]["turnStart"]["request"]
        self.assertEqual(sent["input"][0]["text_elements"], [])
        self.assertNotIn("text_elements", native["input"][0])
        self.session.state["turns"].append({"turnId": "active", "status": "inProgress", "items": []})
        self.session.request = AsyncMock(return_value={"result": {"turnId": "active"}})
        await self.session.rpc("turn/steer", {**native, "expectedTurnId": "active"})
        self.assertEqual(self.session.request.call_args.args[1]["input"][0]["text_elements"], [])

    async def test_private_endpoint_required(self):
        self.path.unlink()
        self.path.symlink_to("elsewhere")
        with self.assertRaises((ValueError, FileNotFoundError)):
            await self.session.attach()

    async def test_response_uses_owner_and_never_native_writer(self):
        await self.session.attach()
        self.session.pending = {7: {"id": 7, "method": "item/commandExecution/requestApproval"}}
        await self.session.respond({"id": 7, "result": {"decision": "accept"}})
        self.assertEqual(self.messages[-1]["targetClientId"], "owner")
        self.assertEqual(self.messages[-1]["params"]["decision"], "accept")
        with self.assertRaises(ValueError):
            await self.session.respond({"id": 8, "result": {"decision": "accept"}})


@unittest.skipUnless(os.name == "nt", "Windows named-pipe App IPC")
class WindowsRouterTests(RouterTests):
    __unittest_skip__ = os.name != "nt"

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(r"\\.\pipe\codex-wechat-fixture-" + uuid.uuid4().hex)
        self.state = conversation()
        self.messages, self.events, self.received_requests = [], [], []
        self.clients = set()
        self.failed = Mock()
        self.nested_response = False
        self.enterContext(patch("app_ipc_windows.PIPE_PATH", str(self.path)))
        core = Path(self.temp.name) / "resources" / "codex.exe"
        core.parent.mkdir(parents=True)
        core.write_text("", encoding="utf-8")
        (core.parent.parent / "ChatGPT.exe").write_text("", encoding="utf-8")
        self.enterContext(patch("app_ipc_windows.discover_app_codex",
                                return_value=str(core)))
        self.verify = self.enterContext(patch("app_ipc_windows.verify_server"))
        loop = asyncio.get_running_loop()
        def protocol():
            reader = asyncio.StreamReader(limit=16 * 1024 * 1024 + 4)
            return asyncio.StreamReaderProtocol(reader, self.handle)
        self.servers = await loop.start_serving_pipe(protocol, str(self.path))
        self.session = AppSession(self.path, "thread", on_event=self.events.append,
            on_request=AsyncMock(side_effect=self.received_requests.append), on_disconnect=self.failed)

    async def asyncTearDown(self):
        await self.session.close()
        for server in self.servers:
            server.close()
        for writer in self.clients:
            writer.close()
            await writer.wait_closed()
        self.temp.cleanup()

    async def test_private_endpoint_required(self):
        self.verify.side_effect = ValueError("untrusted server")
        with self.assertRaisesRegex(ValueError, "untrusted server"):
            await self.session.attach()
        self.assertEqual(self.messages, [])

    async def test_nonlocal_pipe_is_rejected(self):
        self.session.path = Path(r"\\other-host\pipe\codex-ipc")
        with self.assertRaises(ValueError):
            await self.session.attach()
        self.verify.assert_not_called()


@unittest.skipUnless(os.name == "nt", "Windows named-pipe identity check")
class WindowsVerifyServerTests(unittest.IsolatedAsyncioTestCase):
    """The real identity check, against a pipe this process serves itself."""

    async def asyncSetUp(self):
        self.path = r"\\.\pipe\codex-wechat-verify-" + uuid.uuid4().hex
        self.transports = []
        self.loop = asyncio.get_running_loop()

        def protocol():
            return asyncio.StreamReaderProtocol(asyncio.StreamReader())

        self.servers = await self.loop.start_serving_pipe(protocol, self.path)

    async def asyncTearDown(self):
        for server in self.servers:
            server.close()
        for transport in self.transports:
            transport.close()

    async def _connected_handle(self):
        transport, _ = await self.loop.create_pipe_connection(
            lambda: asyncio.StreamReaderProtocol(asyncio.StreamReader()), self.path)
        self.transports.append(transport)
        return transport.get_extra_info("pipe").fileno()

    @staticmethod
    def _own_image():
        # A venv python.exe is a redirector, so sys.executable is not the module
        # the process actually runs; the identity check compares real images.
        import win32api
        import win32con
        import win32process
        process = win32api.OpenProcess(
            win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ,
            False, os.getpid())
        try:
            return win32process.GetModuleFileNameEx(process, 0)
        finally:
            process.Close()

    async def test_accepts_the_executable_that_serves_the_pipe(self):
        from app_ipc_windows import verify_server
        verify_server(await self._connected_handle(), self._own_image())

    async def test_rejects_a_pipe_served_by_another_executable(self):
        from app_ipc_windows import verify_server
        other = Path(os.environ["WINDIR"]) / "System32" / "cmd.exe"
        with self.assertRaisesRegex(ValueError, "不是当前已注册的 Codex App"):
            verify_server(await self._connected_handle(), other)

    async def test_missing_app_executable_is_reported_before_connecting(self):
        import app_ipc_windows
        with tempfile.TemporaryDirectory() as temp:
            core = Path(temp) / "resources" / "codex.exe"
            core.parent.mkdir(parents=True)
            core.write_text("", encoding="utf-8")
            with patch.object(app_ipc_windows, "PIPE_PATH", self.path), \
                 patch.object(app_ipc_windows, "discover_app_codex",
                              return_value=str(core)):
                with self.assertRaisesRegex(ValueError, "找不到已注册的 Codex App 主程序"):
                    await app_ipc_windows.open_pipe(self.path, limit=1024)


@unittest.skipUnless(os.name == "nt", "Windows App endpoint selection")
class WindowsEndpointTests(unittest.TestCase):
    def test_websocket_backend_can_follow_app_only_in_primary_home(self):
        from app_ipc_windows import PIPE_PATH
        with tempfile.TemporaryDirectory() as primary, tempfile.TemporaryDirectory() as other:
            with patch.dict(os.environ, {"CODEX_HOME": primary}):
                config = {"websocketUrl": "ws://127.0.0.1:1234", "env": {"CODEX_HOME": primary}}
                self.assertEqual(ipc_path(config), Path(PIPE_PATH))
                config["env"]["CODEX_HOME"] = other
                self.assertIsNone(ipc_path(config))

    def test_custom_launcher_home_is_never_guessed(self):
        self.assertIsNone(ipc_path({"command": ["custom.exe", "app-server"]}))


class AdoptionTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_writer_conflict_allows_follower_adoption(self):
        backend = CodexBackend({}, "/tmp", lambda _: None)
        backend.rpc = AsyncMock(side_effect=CodexRpcError(-32600, "already has an active writer"))
        app = Mock(thread_id="thread", enabled=False)
        app.thread_result.return_value = {"thread": {"id": "thread"}}
        backend.attach_app = AsyncMock(return_value=app)
        backend.refresh_runtime = AsyncMock()
        backend.refresh_usage = AsyncMock()
        self.assertEqual(await backend.thread("thread"), "thread")
        app.activate.assert_called_once()
        backend.rpc.side_effect = CodexRpcError(-32600, "other rejection")
        with self.assertRaises(CodexRpcError):
            await backend.thread("other")
        backend.attach_app.assert_awaited_once()
        self.assertIs(backend.app, app)

    async def test_app_responses_never_fall_through_after_request_is_resolved(self):
        backend = CodexBackend({}, "/tmp", lambda _: None)
        backend.app = Mock(respond=AsyncMock(side_effect=ValueError("expired")))
        backend.transport.write = AsyncMock()
        with self.assertRaises(ValueError):
            await backend.write({"id": 1, "result": {}})
        backend.transport.write.assert_not_awaited()
