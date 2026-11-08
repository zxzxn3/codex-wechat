"""Synthetic native events only; never request a real compaction."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from codex_wechat.backend import CodexBackend
from codex_wechat.commands import command_compact
from codex_wechat.prompts import COMPACT_STARTED, COMPACT_FINISHED, COMPACT_INTERRUPTED


class CompactionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.notices = []
        self.backend = CodexBackend({}, "/tmp", self.notices.append)
        self.backend.selected_thread = "selected"
        self.backend.compaction_notice = True

    def event(self, method, thread="selected", item="compact"):
        self.backend.event({"method": method, "params": {"threadId": thread,
            "turnId": "turn", "item": {"id": item, "type": "contextCompaction"}}})

    async def test_current_thread_lifecycle_is_not_duplicated(self):
        for method in ("item/started", "item/started", "item/completed", "item/completed", "item/started"):
            self.event(method)
        self.assertEqual(self.notices, [COMPACT_STARTED, COMPACT_FINISHED])
        self.assertIsNone(self.backend.active)

    async def test_compaction_notice_can_be_disabled(self):
        self.backend.compaction_notice = False
        self.event("item/started")
        self.event("item/completed")
        self.assertEqual(self.notices, [])

    async def test_manual_compaction_reports_result_with_automatic_notices_off(self):
        self.backend.compaction_notice = False
        self.backend.rpc = AsyncMock(return_value={})
        self.backend.refresh_runtime = AsyncMock()
        await self.backend.compact("selected")
        self.backend.rpc.assert_awaited_once_with("thread/compact/start", {"threadId": "selected"})
        self.event("item/started")
        self.event("item/completed")
        self.assertEqual(self.notices, [COMPACT_FINISHED])

    async def test_other_thread_and_legacy_event_are_ignored(self):
        self.event("item/started", thread="other")
        self.event("thread/compacted")
        self.assertEqual(self.notices, [])

    async def test_failed_turn_does_not_claim_compaction_success(self):
        self.event("item/started")
        self.backend.event({"method": "turn/completed", "params": {"threadId": "selected",
                            "turn": {"id": "turn", "status": "failed"}}})
        self.event("item/completed")
        self.assertEqual(self.notices, [COMPACT_STARTED, COMPACT_INTERRUPTED])


class CompactCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_command_leaves_feedback_to_compaction_events(self):
        compact = AsyncMock()
        bridge = SimpleNamespace(
            state={"thread": "selected", "inbox": []},
            running=False,
            restarting=False,
            steering=False,
            active_turn=lambda: None,
            ensure_session=AsyncMock(),
            backend=SimpleNamespace(blocked=False, compact=compact),
            reply=Mock(),
            prompt=lambda name: name,
            session_lock=asyncio.Lock(),
        )

        with patch("codex_wechat.commands.ensure_session", AsyncMock()):
            await command_compact(bridge, "/compact", "")

        compact.assert_awaited_once_with("selected")
        bridge.reply.assert_not_called()
