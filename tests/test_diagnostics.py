"""Structured diagnostics stay bounded and never record message content."""
import json
import os
from pathlib import Path
from contextlib import redirect_stdout
from io import StringIO
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import diagnostics
from codex_wechat import Bridge, _log_files, _read_log_updates, _tail_lines, show_logs
from commands import dispatch
from storage import Store
from wechat import WeChat


class CodedError(RuntimeError):
    code = -32603


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "logs" / "bridge.jsonl"
        diagnostics.configure(path=self.path, level="debug", max_bytes=5 * 1024 * 1024)

    def tearDown(self):
        diagnostics.reset()
        self.temp.cleanup()

    def records(self):
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines()]

    def test_jsonl_record_has_only_declared_metadata(self):
        diagnostics.log_event("input.received", component="input", kind="input",
                              chars=12, count=2, ok=True, status="preparing")
        records = self.records()
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["event"], "input.received")
        self.assertEqual(record["component"], "input")
        self.assertEqual(record["chars"], 12)
        self.assertTrue(record["ok"])
        self.assertTrue(record["ts"].endswith("Z"))
        self.assertNotIn("timezone", record)
        self.assertFalse(self.path.read_text(encoding="utf-8").endswith("\n\n"))
        if os.name != "nt":
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_message_prompt_credentials_and_paths_never_reach_log(self):
        sentinels = (
            "PROMPT_SENTINEL_7f935",
            "TOKEN_SENTINEL_7f935",
            "CONTEXT_SENTINEL_7f935",
            "AUTH_SENTINEL_7f935",
            "PATH_SENTINEL_7f935",
            "FILE_SENTINEL_7f935",
        )
        diagnostics.log_event(
            "input.received", component="input",
            text=sentinels[0], prompt=sentinels[0], token=sentinels[1],
            context_token=sentinels[2], Authorization=sentinels[3],
            file_name=sentinels[5], absolute_path="/home/private/" + sentinels[4],
            status="secret " + sentinels[0],
            endpoint="https://example.invalid/?token=" + sentinels[1])
        diagnostics.log_event(
            "startup.failed", component="startup",
            **diagnostics.error_fields(RuntimeError(
                "prompt=" + sentinels[0] + " token=" + sentinels[1])))
        data = self.path.read_text(encoding="utf-8")
        for sentinel in sentinels:
            self.assertNotIn(sentinel, data)
        self.assertNotIn("/home/private/", data)

    def test_request_id_correlates_events_and_unknown_fields_are_dropped(self):
        with diagnostics.request_context("req-abc123"):
            diagnostics.log_event("input.prepare", component="input", count=1,
                                  text="PROMPT_SENTINEL")
            diagnostics.log_event("input.completed", component="input", duration_ms=1.23456)
        records = self.records()
        self.assertEqual([record["request_id"] for record in records],
                         ["req-abc123", "req-abc123"])
        self.assertEqual(records[1]["duration_ms"], 1.235)
        self.assertNotIn("text", records[0])

    def test_invalid_event_and_field_types_are_ignored(self):
        diagnostics.log_event("bad event name", component="input")
        diagnostics.log_event("test.types", count=True, ok="yes", attempt=float("nan"),
                              parts=2.5, status="not a slug")
        records = self.records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["parts"], 2.5)
        for field in ("count", "ok", "attempt", "status"):
            self.assertNotIn(field, records[0])

    def test_rotation_is_bounded(self):
        diagnostics.configure(path=self.path, max_bytes=350)
        for index in range(30):
            diagnostics.log_event("test.rotation", count=index, job_id=f"job-{index}")
        for number in range(1, diagnostics.BACKUP_COUNT + 1):
            self.assertTrue(self.path.with_name(f"{self.path.name}.{number}").exists())
        self.assertFalse(self.path.with_name(
            f"{self.path.name}.{diagnostics.BACKUP_COUNT + 1}").exists())

    def test_log_write_failure_is_silent(self):
        with patch("diagnostics.os.open", side_effect=OSError("disk full")):
            diagnostics.log_event("test.write_failed", component="bridge")

    def test_busy_process_lock_drops_diagnostic_without_writing(self):
        with patch("storage._lock_descriptor", side_effect=BlockingIOError()):
            diagnostics.log_event("test.lock_busy", component="bridge")
        self.assertFalse(self.path.exists())
        diagnostics.log_event("test.lock_available", component="bridge")
        self.assertEqual([record["event"] for record in self.records()],
                         ["test.lock_available"])

    def test_endpoint_schema_rejects_paths_and_unknown_routes(self):
        for endpoint in ("/etc/passwd", r"C:\private\file.txt", "report.txt",
                         "ilink/bot/private_file", "ilink/bot/getconfig?token=secret"):
            diagnostics.log_event("test.endpoint", endpoint=endpoint)
        self.assertTrue(all("endpoint" not in record for record in self.records()))

    def test_follow_reads_rotated_tail_once_and_replacement_from_start(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("first\n", encoding="utf-8")
        positions = {}
        with redirect_stdout(StringIO()):
            _read_log_updates([self.path], positions, initial=True)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write("before rotation\n")
        backup = self.path.with_name(self.path.name + ".1")
        self.path.replace(backup)
        self.path.write_text("replacement longer than the original file\n", encoding="utf-8")
        output = StringIO()
        with redirect_stdout(output):
            _read_log_updates([self.path, backup], positions)
            _read_log_updates([self.path, backup], positions)
        self.assertNotIn("first\n", output.getvalue())
        self.assertEqual(output.getvalue().count("before rotation\n"), 1)
        self.assertEqual(output.getvalue().count("replacement longer than the original file\n"), 1)

    def test_follow_does_not_skip_append_while_initial_tail_is_printed(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("old\n", encoding="utf-8")
        positions = {}
        def print_and_append(*args, **kwargs):
            if args == ("old",):
                with self.path.open("a", encoding="utf-8") as stream:
                    stream.write("raced append\n")
        with patch("codex_wechat.print", side_effect=print_and_append):
            _read_log_updates([self.path], positions, initial=True)
        output = StringIO()
        with redirect_stdout(output):
            _read_log_updates([self.path], positions)
        self.assertEqual(output.getvalue(), "raced append\n")

    def test_error_fields_exclude_exception_message(self):
        fields = diagnostics.error_fields(CodedError("PROMPT_SENTINEL_7f935"))
        self.assertEqual(fields, {"error_type": "CodedError", "error_code": "-32603"})
        self.assertNotIn("PROMPT_SENTINEL_7f935", json.dumps(fields))

    def test_log_files_include_rotations_and_legacy_service_log(self):
        diagnostics.log_event("bridge.start", component="bridge")
        for number in range(1, diagnostics.BACKUP_COUNT + 1):
            self.path.with_name(f"{self.path.name}.{number}").write_text("", encoding="utf-8")
        (self.path.parent / "service.log").write_text("console\n", encoding="utf-8")
        (self.path.parent / "service.log.1").write_text("old console\n", encoding="utf-8")
        names = [path.name for path in _log_files(include_service=True)]
        self.assertEqual(names, ["bridge.jsonl", "bridge.jsonl.1", "bridge.jsonl.2",
                                 "bridge.jsonl.3", "service.log", "service.log.1"])
        self.assertEqual([path.name for path in _log_files()],
                         ["bridge.jsonl", "bridge.jsonl.1", "bridge.jsonl.2",
                          "bridge.jsonl.3"])

    def test_logs_command_shows_service_output_only_when_requested(self):
        diagnostics.log_event("bridge.start", component="bridge")
        (self.path.parent / "service.log").write_text("supervisor started\n", encoding="utf-8")
        output = StringIO()
        with redirect_stdout(output):
            show_logs()
        text = output.getvalue()
        self.assertIn('"event":"bridge.start"', text)
        self.assertNotIn("supervisor started", text)
        output = StringIO()
        with redirect_stdout(output):
            show_logs(include_service=True)
        self.assertIn("supervisor started", output.getvalue())

    def test_tail_reads_only_the_bounded_tail(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("old\n" * 1000 + "recent\n", encoding="utf-8")
        self.assertEqual(_tail_lines(self.path, limit=1), ["recent"])

    def test_windows_logs_command_uses_the_shared_viewer(self):
        script = (Path(__file__).parents[1] / "codex-wechat.ps1").read_text(encoding="utf-8")
        branch = script.split("'logs' {", 1)[1].split("'uninstall' {", 1)[0]
        self.assertIn("@($Agent, 'logs')", branch)
        self.assertIn("--follow", branch)
        self.assertIn("--service", branch)
        self.assertNotIn("Get-Content", branch)


class DiagnosticsIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "data" / "logs" / "bridge.jsonl"
        diagnostics.configure(path=self.path, level="debug")
        self.store = Store(str(Path(self.temp.name) / "state"))

    def tearDown(self):
        self.store.lock.close()
        diagnostics.reset()
        self.temp.cleanup()

    def records(self):
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines()]

    async def test_received_prompt_is_represented_by_counts_only(self):
        bridge = Bridge(self.store, {"user": "owner"}, {})
        sentinel = "PROMPT_SENTINEL_7f935"
        message = {"message_id": "1", "from_user_id": "owner", "message_type": 1,
                   "context_token": "CONTEXT_SENTINEL_7f935",
                   "item_list": [{"type": 1, "text_item": {"text": sentinel}}]}
        await bridge.receive({"msgs": [message]})
        record = next(item for item in self.records() if item["event"] == "input.received")
        self.assertEqual(record["chars"], len(sentinel))
        data = self.path.read_text(encoding="utf-8")
        self.assertNotIn(sentinel, data)
        self.assertNotIn("CONTEXT_SENTINEL_7f935", data)
        self.assertIn("request_id", record)

    async def test_output_uses_the_input_request_id(self):
        bridge = Bridge(self.store, {"user": "owner"}, {})
        bridge.state["context"] = "context"
        with diagnostics.request_context("req-output"):
            await bridge.deliver_answer("answer")
        record = next(item for item in self.records()
                      if item["event"] == "output.enqueued")
        self.assertEqual(record["request_id"], "req-output")
        self.assertEqual(bridge.state["outbox"][0]["requestId"], "req-output")

    async def test_native_progress_links_primary_and_steered_inputs_without_contextvar(self):
        bridge = Bridge(self.store, {"user": "owner"}, {})
        bridge.state.update(context="context", thread="thread")
        backend = bridge.backend
        backend.selected_thread = "thread"
        backend.active = backend.new_turn("thread", "turn", owned=False)
        for identity in ("req-original", "req-steered"):
            bridge.dispatch_input({"requestId": identity}, "accepted", "turn", backend)
        backend.event({"method": "item/completed", "params": {
            "threadId": "thread", "turnId": "turn", "item": {
                "type": "agentMessage", "id": "progress", "phase": "commentary", "text": "working"}}})
        entry = bridge.state["outbox"][0]
        self.assertEqual(entry["requestId"], "req-original")
        linked = {record["request_id"] for record in self.records()
                  if record["event"] in ("output.enqueued", "output.related")
                  and record.get("job_id") == entry["group"]}
        self.assertEqual(linked, {"req-original", "req-steered"})

    async def test_rejected_input_and_control_outcomes_are_logged(self):
        bridge = Bridge(self.store, {"user": "owner"}, {})
        message = {"message_id": "queue-empty", "from_user_id": "owner",
                   "message_type": 1, "context_token": "ctx",
                   "item_list": [{"type": 1, "text_item": {"text": "/queue"}}]}
        await bridge.receive({"msgs": [message]})
        rejected = next(item for item in self.records()
                        if item["event"] == "input.rejected")
        self.assertEqual(rejected["status"], "missing_argument")
        await dispatch(bridge, "/help", received={"request_id": "req-help"})
        await dispatch(bridge, "/not-a-command", received={"request_id": "req-unknown"})
        records = self.records()
        self.assertTrue(any(item["event"] == "control.completed" and
                            item["request_id"] == "req-help" for item in records))
        self.assertTrue(any(item["event"] == "control.rejected" and
                            item["request_id"] == "req-unknown" for item in records))

    async def test_backend_disconnect_is_logged(self):
        bridge = Bridge(self.store, {"user": "owner"}, {})
        bridge.backend.connection_lost(RuntimeError("disconnect"))
        record = next(item for item in self.records()
                      if item["event"] == "backend.disconnected")
        self.assertEqual(record["error_type"], "RuntimeError")
        self.assertFalse(record["connected"])

    async def test_wechat_logs_endpoint_without_query_or_credentials(self):
        token = "TOKEN_SENTINEL_7f935"
        process = SimpleNamespace(returncode=0, stdin=None)
        client = WeChat({"token": token, "base_url": "https://ilinkai.weixin.qq.com"})
        with patch("wechat.asyncio.create_subprocess_exec",
                   AsyncMock(return_value=process)), \
             patch("wechat.communicate",
                   AsyncMock(return_value=(b'{"ret":0}\n200', b""))):
            await client.request("ilink/bot/getconfig?token=" + token)
        record = next(item for item in self.records() if item["event"] == "wechat.request")
        self.assertEqual(record["endpoint"], "ilink/bot/getconfig")
        self.assertTrue(record["ok"])
        self.assertEqual(record["http_status"], 200)
        data = self.path.read_text(encoding="utf-8")
        self.assertNotIn(token, data)
        self.assertNotIn("?token=", data)

    async def test_successful_poll_is_debug_only(self):
        process = SimpleNamespace(returncode=0, stdin=None)
        client = WeChat({"token": "token",
                         "base_url": "https://ilinkai.weixin.qq.com"})
        with patch("wechat.asyncio.create_subprocess_exec",
                   AsyncMock(return_value=process)), \
             patch("wechat.communicate",
                   AsyncMock(return_value=(b'{"ret":0}\n200', b""))):
            diagnostics.configure(level="info")
            await client.request("ilink/bot/getupdates")
            self.assertFalse(any(item["event"] == "wechat.request"
                                 for item in self.records()))
            diagnostics.configure(level="debug")
            await client.request("ilink/bot/getupdates")
        self.assertTrue(any(item["event"] == "wechat.request"
                            for item in self.records()))

    async def test_successful_slow_poll_is_debug_only(self):
        process = SimpleNamespace(returncode=0, stdin=None)
        client = WeChat({"token": "token",
                         "base_url": "https://ilinkai.weixin.qq.com"})
        with patch("wechat.asyncio.create_subprocess_exec",
                   AsyncMock(return_value=process)), \
             patch("wechat.communicate",
                   AsyncMock(return_value=(b'{"ret":0}\n200', b""))), \
             patch("wechat.time.perf_counter", side_effect=[0, 18]):
            diagnostics.configure(level="info")
            await client.request("ilink/bot/getupdates")
        self.assertFalse(any(item["event"] == "wechat.request"
                             for item in self.records()))


if __name__ == "__main__":
    unittest.main()
