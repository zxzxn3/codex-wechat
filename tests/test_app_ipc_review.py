"""Independent counterexamples for the App IPC review."""
import asyncio
import copy
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock

from app_ipc import AppSession, VERSIONS
from backend import CodexBackend
from codex_transport import CodexRpcError


def turn(turn_id, *, status="completed", items=None):
    return {"turnId": turn_id, "status": status, "items": items or [],
            "completedItemIds": {}}


def item(item_id, text):
    return {"id": item_id, "type": "agentMessage", "phase": "final_answer",
            "text": text, "completedAtMs": 1}


def canonical(turns, *, complete=False):
    entities, entries = {}, []
    for index, value in enumerate(turns):
        key = f"entity-{index}"
        entities[key] = value
        entries.append({"key": key, "value": key})
    return {"id": "thread", "hostId": "local", "requests": [],
            "turnHistory": {"kind": "canonical", "history": {
                "entitiesByKey": entities, "entries": [],
                "isComplete": complete, "generation": 1,
                "islands": [{"id": "tail", "entries": entries}]}}}


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "ipc.sock"
        self.state = canonical([turn("base", items=[item("base-item", "base")])])
        self.server = await asyncio.start_unix_server(self.handle, path=self.path)
        self.writers = set()
        self.events = []
        self.session = AppSession(self.path, "thread", on_event=self.events.append,
            on_request=self.on_request, on_disconnect=lambda error: None)

    async def asyncTearDown(self):
        await self.session.close()
        self.server.close()
        for writer in self.writers:
            writer.close()
        await self.server.wait_closed()
        self.temp.cleanup()

    async def on_request(self, request):
        pass

    async def frame(self, writer, message):
        payload = json.dumps(message).encode()
        writer.write(struct.pack("<I", len(payload)) + payload)
        await writer.drain()

    async def snapshot(self, writer, state, revision):
        await self.frame(writer, {"type": "broadcast",
            "method": "thread-stream-state-changed",
            "version": VERSIONS["thread-stream-state-changed"],
            "sourceClientId": "owner", "params": {
                "hostId": "local", "conversationId": "thread",
                "change": {"type": "snapshot", "revision": revision,
                           "conversationState": state}}})

    async def handle(self, reader, writer):
        self.writers.add(writer)
        try:
            while True:
                size = struct.unpack("<I", await reader.readexactly(4))[0]
                message = json.loads(await reader.readexactly(size))
                if message["type"] == "broadcast":
                    await self.snapshot(writer, copy.deepcopy(self.state), 1)
                    continue
                method = message["method"]
                if method == "initialize":
                    result, owner = {"clientId": "client"}, "client"
                else:
                    result, owner = {"supportsUntrustedAppInput": True}, "owner"
                await self.frame(writer, {"type": "response",
                    "requestId": message["requestId"], "resultType": "success",
                    "method": method, "handledByClientId": owner,
                    "result": result})
        except (asyncio.IncompleteReadError, ConnectionError):
            pass

    async def test_loading_older_canonical_history_does_not_replay_old_items(self):
        await self.session.attach()
        self.session.activate()
        self.events.clear()
        self.state = canonical([
            turn("older", items=[item("old-item", "old")]),
            turn("base", items=[item("base-item", "base")])])
        await self.session.broadcast({"type": "broadcast",
            "method": "thread-stream-state-changed",
            "version": VERSIONS["thread-stream-state-changed"],
            "sourceClientId": "owner", "params": {
                "hostId": "local", "conversationId": "thread",
                "change": {"type": "snapshot", "revision": 2,
                           "conversationState": copy.deepcopy(self.state)}}})
        emitted = [(event["method"], event["params"].get("item", {}).get("id"))
                   for event in self.events]
        self.assertNotIn(("item/completed", "old-item"), emitted)
        turns = [(event["method"], event["params"].get("turn", {}).get("id"))
                 for event in self.events]
        self.assertNotIn(("turn/started", "older"), turns)
        self.assertNotIn(("turn/completed", "older"), turns)


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_request_handler_failure_does_not_kill_later_requests(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "unused.sock"
        seen = []

        async def on_request(request):
            seen.append(request["id"])
            if len(seen) == 1:
                raise RuntimeError("sender failed")

        session = AppSession(path, "thread", on_event=lambda event: None,
            on_request=on_request, on_disconnect=lambda error: None)
        session.owner = "owner"
        session.state = {"id": "thread", "hostId": "local", "turns": [],
            "requests": [{"id": "r1",
                "method": "item/commandExecution/requestApproval", "params": {}}]}
        session.activate()
        def consume(task):
            if not task.cancelled():
                task.exception()
        session.interaction_worker.add_done_callback(consume)
        await asyncio.sleep(0.05)
        session.state = {"id": "thread", "hostId": "local", "turns": [],
            "requests": [{"id": "r2",
                "method": "item/commandExecution/requestApproval", "params": {}}]}
        session.publish()
        await asyncio.sleep(0.05)
        self.assertEqual(seen, ["r1", "r2"])
        self.assertFalse(session.interaction_worker.done())
        await session.close()


class AdoptionOrderTests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_requests_are_activated_after_runtime_selection(self):
        backend = CodexBackend({}, "/tmp", lambda _: None)
        backend.rpc = AsyncMock(side_effect=CodexRpcError(
            -32600, "already has an active writer"))
        activated = []
        app = Mock(thread_id="thread", enabled=False, connected=True)
        app.thread_result.return_value = {"thread": {"id": "thread"}}
        app.settings = AsyncMock()
        app.activate = Mock(side_effect=lambda: activated.append(backend.active))
        backend.attach_app = AsyncMock(return_value=app)
        backend.refresh_runtime = AsyncMock(side_effect=lambda: setattr(
            backend, "active", {"turn": "desktop-turn"}))
        backend.refresh_usage = AsyncMock()
        await backend.thread("thread")
        self.assertEqual(activated, [{"turn": "desktop-turn"}])
