"""Native questions via synthetic server requests only; no desktop or model."""
import unittest
from unittest.mock import AsyncMock

from backend import CodexBackend
from command_specs import REGISTRY, command_lane
from questions import QUESTION_METHOD


class QuestionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.notices = []
        self.backend = CodexBackend({}, "/tmp", self.notices.append)
        self.backend.selected_thread = "selected"
        self.backend.active = self.backend.new_turn("selected", "turn", owned=False)
        self.backend.shared = True
        self.backend.write = AsyncMock()

    def request(self, questions=None, thread="selected", identity=1):
        return {"id": identity, "method": QUESTION_METHOD, "params": {
            "threadId": thread, "turnId": "turn", "itemId": "item", "isBlocking": True,
            "questions": questions or [{"id": "where", "header": "环境",
                "question": "运行在哪里？", "options": [
                    {"label": "Windows", "description": "原生运行"},
                    {"label": "WSL", "description": "Linux 环境"}]}]}}

    async def test_desktop_question_and_native_response(self):
        await self.backend.server_request(self.request())
        token = next(iter(self.backend.pending))
        self.assertIn("运行在哪里？", self.notices[0])
        self.assertIn(f"`/answer {token} {{选项或文字}}`", self.notices[0])
        self.assertFalse(self.backend.write.called)
        self.assertTrue(await self.backend.answer_question(token, "1"))
        self.backend.write.assert_awaited_once_with({"id": 1, "result": {
            "answers": {"where": {"answers": ["Windows"]}}}})
        self.assertFalse(await self.backend.answer_question(token, "2"))

    async def test_multiple_questions_rotate_tokens_and_submit_together(self):
        questions = [{"id": "a", "header": "A", "question": "第一题"},
                     {"id": "b", "header": "B", "question": "第二题"}]
        await self.backend.server_request(self.request(questions))
        old = next(iter(self.backend.pending))
        await self.backend.answer_question(old, "自由文字")
        token = next(iter(self.backend.pending))
        self.assertNotEqual(old, token)
        self.assertFalse(await self.backend.answer_question(old, "迟到"))
        self.backend.write.assert_not_called()
        await self.backend.answer_question(token, "其他答案")
        self.assertEqual(self.backend.write.call_args.args[0]["result"]["answers"], {
            "a": {"answers": ["自由文字"]}, "b": {"answers": ["其他答案"]}})

    async def test_invalid_option_keeps_question(self):
        await self.backend.server_request(self.request())
        token = next(iter(self.backend.pending))
        with self.assertRaises(ValueError):
            await self.backend.answer_question(token, "3")
        self.assertIn(token, self.backend.pending)
        await self.backend.answer_question(token, "其他系统")
        self.assertEqual(self.backend.write.call_args.args[0]["result"]["answers"],
                         {"where": {"answers": ["其他系统"]}})

    async def test_resolved_source_and_connection_invalidation(self):
        await self.backend.server_request(self.request())
        token = next(iter(self.backend.pending))
        self.backend.event({"method": "serverRequest/resolved", "params": {
            "threadId": "other", "requestId": 1}})
        self.assertIn(token, self.backend.pending)
        self.backend.event({"method": "serverRequest/resolved", "params": {
            "threadId": "selected", "requestId": 1}})
        self.assertFalse(await self.backend.answer_question(token, "1"))
        await self.backend.server_request(self.request(identity=2))
        token = next(iter(self.backend.pending))
        self.backend.selection_epoch += 1
        self.assertFalse(await self.backend.answer_question(token, "1"))
        await self.backend.server_request(self.request(identity=3))
        self.backend.connection_lost(RuntimeError("disconnect"))
        self.assertFalse(self.backend.pending)

    async def test_other_thread_is_ignored_and_approvals_stay_separate(self):
        await self.backend.server_request(self.request(thread="other"))
        self.assertFalse(self.backend.pending)
        self.assertFalse(self.notices)
        await self.backend.server_request(self.request())
        token = next(iter(self.backend.pending))
        self.assertFalse(await self.backend.answer_approval(token, "accept"))
        self.assertIn(token, self.backend.pending)
        self.backend.pending["approval"] = {"id": 4, "method": "item/fileChange/requestApproval"}
        self.assertFalse(await self.backend.answer_question("approval", "1"))

    async def test_duplicate_request_and_failed_write_do_not_replay(self):
        await self.backend.server_request(self.request())
        await self.backend.server_request(self.request())
        self.assertEqual(len(self.notices), 1)
        token = next(iter(self.backend.pending))
        self.backend.write.side_effect = OSError("disconnected")
        with self.assertRaises(OSError):
            await self.backend.answer_question(token, "1")
        self.assertFalse(await self.backend.answer_question(token, "1"))

    async def test_answer_command_uses_urgent_lane_without_connecting(self):
        self.assertEqual(command_lane("/answer", "token 1"), "urgent")
        self.assertFalse(REGISTRY["/answer"].needs_backend)
