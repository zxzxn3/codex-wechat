"""Offline contracts for typing, quoted input, voice, and video."""
import asyncio
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from codex_wechat import Bridge
from command_specs import REGISTRY
from media import Media, split_answer
from storage import Store
from wechat import WeChat
from wechat_content import remember_quote


class FakeChannel:
    def __init__(self, ticket="ticket"):
        self.ticket = ticket
        self.statuses = []

    async def typing_ticket(self, user, context=None):
        return self.ticket

    async def set_typing(self, user, ticket, status):
        self.statuses.append((user, ticket, status))


class WeChatFeatureTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.tasks = []

    async def asyncTearDown(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.store.lock.close()
        self.temp.cleanup()

    def message(self, identity, item_list, *, reference=None):
        item = {"type": 1, "text_item": {"text": "question"}}
        if reference is not None:
            item["ref_msg"] = reference
        return {"message_id": identity, "from_user_id": "owner", "message_type": 1,
                "context_token": "context", "item_list": item_list or [item]}

    async def test_quoted_partial_voice_and_cached_output(self):
        channel = FakeChannel()
        bridge = Bridge(self.store, {"user": "owner", "token": "token"}, {}, channel=channel)
        bridge.state["context"] = "context"

        original = self.message("10", [{"type": 1, "text_item": {
            "text": "Original sentence with target words."}}])
        await bridge.receive({"msgs": [original]})
        bridge.state["inbox"].clear()
        partial = {
            "start": "target", "end": "target", "startindex": 0, "endindex": 0,
            "quotemd5": hashlib.md5(b"target").hexdigest(),
        }
        quoted = self.message("11", [{"type": 1, "text_item": {"text": "change it"},
            "ref_msg": {"svr_id": "10", "partial_text": partial}}])
        voice = self.message("12", [{"type": 3, "voice_item": {"text": "voice prompt"}}])
        await bridge.receive({"msgs": [quoted, voice]})

        self.assertEqual(bridge.state["inbox"][0]["text"],
                         "[引用消息]\ntarget\n[/引用消息]\n\nchange it")
        self.assertEqual(bridge.state["inbox"][1]["text"], "voice prompt")

        bridge.reply("answer")
        bridge.channel.send = AsyncMock(return_value={"message_id": "900"})
        self.tasks.append(asyncio.create_task(bridge.sender()))
        await asyncio.wait_for(bridge.outbox_drained.wait(), 1)
        self.assertEqual(bridge.state["quoteCache"]["900"], "answer")

    async def test_item_message_id_is_used_for_dedup_and_quote_lookup(self):
        channel = FakeChannel()
        bridge = Bridge(self.store, {"user": "owner", "token": "token"}, {}, channel=channel)
        original = {
            "from_user_id": "owner", "message_type": 1, "context_token": "context",
            "item_list": [{"type": 1, "msg_id": "111", "text_item": {"text": "Original"}}],
        }
        await bridge.receive({"msgs": [original, original]})
        self.assertEqual(len(bridge.state["inbox"]), 1)
        quoted = {
            "message_id": "112", "from_user_id": "owner", "message_type": 1,
            "context_token": "context",
            "item_list": [{"type": 1, "text_item": {"text": "follow up"},
                           "ref_msg": {"svr_id": "111"}}],
        }
        await bridge.receive({"msgs": [quoted]})
        self.assertEqual(
            bridge.state["inbox"][1]["text"],
            "[引用消息]\nOriginal\n[/引用消息]\n\nfollow up",
        )

    async def test_typing_indicator_uses_reference_count_and_cancel(self):
        channel = FakeChannel()
        bridge = Bridge(self.store, {"user": "owner", "token": "token"}, {}, channel=channel)
        self.assertTrue(await bridge.typing.start("owner", "context"))
        self.assertTrue(await bridge.typing.start("owner", "context"))
        await bridge.typing.stop()
        self.assertEqual(channel.statuses, [("owner", "ticket", 1)])
        await bridge.typing.stop()
        self.assertEqual(channel.statuses,
                         [("owner", "ticket", 1), ("owner", "ticket", 2)])

    async def test_typing_api_contract(self):
        client = WeChat({"token": "token", "base_url": "https://ilinkai.weixin.qq.com"})
        client.request = AsyncMock(side_effect=[{"typing_ticket": "ticket"}, {}])
        self.assertEqual(await client.typing_ticket("owner", "context"), "ticket")
        await client.set_typing("owner", "ticket", 1)
        first, second = client.request.await_args_list
        self.assertEqual(first.args[0], "ilink/bot/getconfig")
        self.assertEqual(first.args[1],
                         {"ilink_user_id": "owner", "context_token": "context"})
        self.assertEqual(second.args[0], "ilink/bot/sendtyping")
        self.assertEqual(second.args[1],
                         {"ilink_user_id": "owner", "typing_ticket": "ticket", "status": 1})

    async def test_video_snapshot_and_upload_item(self):
        with tempfile.TemporaryDirectory() as source:
            path = Path(source) / "clip.mp4"
            path.write_bytes(b"not-a-real-video")
            channel = type("UploadChannel", (), {
                "request": AsyncMock(return_value={
                    "upload_full_url": "https://novac2c.cdn.weixin.qq.com/c2c/upload",
                }),
            })()
            media = Media(self.store, channel)
            asset = media.snapshot(str(path), source, "auto")
            self.assertEqual(asset["kind"], "video")
            media.transfer = AsyncMock(return_value=(b"", "download-param"))
            with patch("media.cipher", return_value=b"encrypted-video"):
                item = await media.upload({"asset": asset, "user": "owner"})
            self.assertEqual(item["type"], 5)
            self.assertIn("video_item", item)
            self.assertEqual(channel.request.await_args.args[1]["media_type"], 2)
        self.assertEqual(split_answer(
            'answer\nWECHAT_MEDIA: {"path":"/tmp/clip.mp4","kind":"video"}')[1],
            [{"path": "/tmp/clip.mp4", "kind": "video"}])
        self.assertIn("/sendvideo", REGISTRY)

    async def test_remember_quote_is_bounded(self):
        state = {}
        for number in range(205):
            remember_quote(state, number, f"message {number}")
        self.assertEqual(len(state["quoteCache"]), 200)
        self.assertEqual(next(iter(state["quoteCache"])), "5")
