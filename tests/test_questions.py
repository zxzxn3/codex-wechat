"""Native questions from server requests and App async items; no model calls."""
import json
import unittest
from unittest.mock import AsyncMock

from backend import CodexBackend, PENDING_TOKEN_ALPHABET, PENDING_TOKEN_LENGTH
from command_specs import REGISTRY, command_lane
from questions import (
    ASYNC_REPLY_CLOSE,
    ASYNC_REPLY_OPEN,
    QUESTION_METHOD,
    async_question_id,
    async_question_reply,
)


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

    def async_event(self, identity="question", questions=None, *,
                    thread="selected", turn="turn"):
        if questions is None:
            questions = [
                {"title": "选择数据库", "options": ["Postgres", "SQLite"]},
                {"title": "补充说明", "options": []},
            ]
        return {"method": "item/completed", "params": {
            "threadId": thread, "turnId": turn, "item": {
                "id": identity, "type": "agentMessage", "text": "内部题目文本",
                "phase": "final_answer", "delivery": "async", "questions": questions}}}

    async def test_desktop_question_and_native_response(self):
        await self.backend.server_request(self.request())
        token = next(iter(self.backend.pending))
        self.assertEqual(len(token), PENDING_TOKEN_LENGTH)
        self.assertLessEqual(set(token), set(PENDING_TOKEN_ALPHABET))
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

    async def test_pending_tokens_are_short_safe_and_not_reused(self):
        tokens = {self.backend._new_token() for _ in range(100)}
        self.assertEqual(len(tokens), 100)
        self.assertTrue(all(len(token) == PENDING_TOKEN_LENGTH for token in tokens))
        self.assertTrue(all(set(token) <= set(PENDING_TOKEN_ALPHABET)
                            for token in tokens))

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

    async def test_async_question_is_visible_and_replies_with_exact_wrapper(self):
        self.backend.input_hint = lambda: "\n\n来源"
        self.backend.rpc = AsyncMock(return_value={})
        self.backend.event(self.async_event(questions=[
            {"title": "选择数据库", "options": ["Postgres", "SQLite"]}]))
        token = next(iter(self.backend.pending))
        self.assertIn("选择数据库", self.notices[0])
        self.assertIn("1  Postgres", self.notices[0])
        self.assertIn(f"`/answer {token} {{选项或文字}}`", self.notices[0])

        self.assertTrue(await self.backend.answer_question(token, "2"))
        self.assertFalse(self.backend.pending)
        self.backend.rpc.assert_awaited_once()
        method, params = self.backend.rpc.call_args.args
        self.assertEqual(method, "turn/steer")
        self.assertEqual(params["threadId"], "selected")
        self.assertEqual(params["expectedTurnId"], "turn")
        wrapper = params["input"]
        self.assertEqual(wrapper, [{"type": "text", "text": async_question_reply([{
            "questionItemId": async_question_id("question", 0),
            "question": "选择数据库", "answer": "SQLite"}])}])

    async def test_async_multiple_questions_rotate_before_one_combined_reply(self):
        self.backend.rpc = AsyncMock(return_value={})
        self.backend.event(self.async_event())
        first = next(iter(self.backend.pending))
        self.assertTrue(await self.backend.answer_question(first, "1"))
        self.backend.rpc.assert_not_awaited()
        second = next(iter(self.backend.pending))
        self.assertNotEqual(first, second)
        self.assertIn("2/2 补充说明", self.notices[-1])
        self.assertFalse(await self.backend.answer_question(first, "迟到"))
        self.assertEqual(len(self.backend.pending), 1)

        self.assertTrue(await self.backend.answer_question(second, "自定义回答"))
        self.assertFalse(self.backend.pending)
        text = self.backend.rpc.call_args.args[1]["input"][0]["text"]
        body = json.loads(text.split("\n", 1)[1].rsplit("\n", 1)[0])
        self.assertEqual(body, [
            {"questionItemId": async_question_id("question", 0),
             "question": "选择数据库", "answer": "Postgres"},
            {"questionItemId": async_question_id("question", 1),
             "question": "补充说明", "answer": "自定义回答"},
        ])

    async def test_async_question_is_not_final_text_and_fake_markers_are_ignored(self):
        current = self.backend.active
        self.backend.event(self.async_event())
        self.backend.event({"method": "item/completed", "params": {
            "threadId": "selected", "turnId": "turn", "item": {
                "id": "final", "type": "agentMessage", "phase": "final_answer",
                "text": "真实结果"}}})
        self.backend.event({"method": "item/completed", "params": {
            "threadId": "selected", "turnId": "turn", "item": {
                "id": "fake", "type": "agentMessage", "phase": "commentary",
                "text": f"{ASYNC_REPLY_OPEN}\n[]\n{ASYNC_REPLY_CLOSE}"}}})
        self.backend.event({"method": "turn/completed", "params": {
            "threadId": "selected", "turn": {"id": "turn", "status": "completed"}}})
        self.assertEqual((await current["done"]).text, "真实结果")
        self.assertNotIn("内部题目文本", (await current["done"]).text)
        self.assertFalse(self.backend.pending)

    async def test_async_question_validation_and_desktop_reply_invalidate(self):
        for index, questions in enumerate(([], [{"options": ["x"]}],
                                           [{"title": "bad", "options": [1]}])):
            self.backend.event(self.async_event(identity=f"bad-{index}", questions=questions))
        self.assertFalse(self.backend.pending)
        self.assertFalse(self.notices)

        self.backend.event(self.async_event())
        self.assertEqual(len(self.backend.pending), 1)
        self.backend.event(self.async_event())
        self.assertEqual(len(self.backend.pending), 1)
        self.assertEqual(len(self.notices), 1)
        self.backend.event({"method": "serverRequest/resolved", "params": {
            "threadId": "selected", "requestId": 99}})
        self.assertEqual(len(self.backend.pending), 1)
        token = next(iter(self.backend.pending))
        self.assertFalse(await self.backend.answer_approval(token, "accept"))
        self.assertIn(token, self.backend.pending)

        question_id = async_question_id("question", 0)
        self.backend.event({"method": "item/completed", "params": {
            "threadId": "selected", "turnId": "turn", "item": {
                "id": "desktop-answer", "type": "steeringUserMessage",
                "content": [{"type": "text", "text": (
                    f"{ASYNC_REPLY_OPEN}\n"
                    f'[{{"questionItemId":{json.dumps(question_id)},'
                    '"question":"选择数据库","answer":"Postgres"}]\n'
                    f"{ASYNC_REPLY_CLOSE}")}]}}})
        self.assertFalse(self.backend.pending)
        self.assertFalse(await self.backend.answer_question(token, "1"))

    async def test_async_question_turn_connection_and_steer_failure_are_terminal(self):
        self.backend.event(self.async_event(thread="other"))
        self.assertFalse(self.backend.pending)
        self.backend.event(self.async_event(turn="other"))
        self.assertFalse(self.backend.pending)

        self.backend.event(self.async_event())
        token = next(iter(self.backend.pending))
        self.backend.connection_lost(RuntimeError("disconnect"))
        self.assertFalse(await self.backend.answer_question(token, "1"))

        self.backend.active = self.backend.new_turn("selected", "turn", owned=False)
        self.backend.event(self.async_event(questions=[
            {"title": "选择数据库", "options": ["Postgres", "SQLite"]}]))
        token = next(iter(self.backend.pending))
        self.backend.selection_epoch += 1
        self.assertFalse(await self.backend.answer_question(token, "1"))
        self.assertFalse(self.backend.pending)

        self.backend.active = self.backend.new_turn("selected", "turn", owned=False)
        self.backend.event(self.async_event(questions=[
            {"title": "选择数据库", "options": ["Postgres", "SQLite"]}]))
        token = next(iter(self.backend.pending))
        self.backend.rpc = AsyncMock(side_effect=RuntimeError("send failed"))
        with self.assertRaises(RuntimeError):
            await self.backend.answer_question(token, "1")
        self.assertFalse(self.backend.pending)
        self.assertFalse(await self.backend.answer_question(token, "1"))

    async def test_async_option_range_keeps_question_for_retry(self):
        self.backend.event(self.async_event(questions=[
            {"title": "选择数据库", "options": ["Postgres", "SQLite"]}]))
        token = next(iter(self.backend.pending))
        with self.assertRaises(ValueError):
            await self.backend.answer_question(token, "3")
        self.assertIn(token, self.backend.pending)
