"""App-owned session adoption; fake local router only, never real Codex."""
import asyncio
import copy
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from app_ipc import AppSession, MAX_FRAME, VERSIONS, apply_patches, ipc_path, turns
from backend import CodexBackend
from codex_transport import CodexRpcError


def conversation():
    return {"id": "thread", "hostId": "local", "title": "fixture", "cwd": "/tmp",
        "latestModel": "model", "latestReasoningEffort": "high",
        "threadRuntimeStatus": {"type": "idle"}, "requests": [],
        "turns": [{"turnId": "old", "status": "completed", "items": [
            {"type": "agentMessage", "id": "old-item", "phase": "final_answer", "text": "old"}]}]}


class StateTests(unittest.TestCase):
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
        with patch("app_ipc.os.name", "posix"):
            self.assertIsNone(ipc_path({"command": ["codex-deepseek", "app-server"]}))
            self.assertEqual(ipc_path({"command": ["custom", "app-server"],
                "env": {"CODEX_HOME": "/tmp/home"}}), Path("/tmp/home/ipc/ipc.sock"))
            self.assertIsNone(ipc_path({"websocketUrl": "ws://127.0.0.1:1234"}))


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
                    native = {"result": {"turn": {"id": "new"}}} if method == "thread-follower-start-turn" else {"ok": True}
                    result = {"method": method, "result": native}
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
