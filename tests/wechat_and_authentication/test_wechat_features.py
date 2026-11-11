"""Offline contracts for typing, quoted input, voice, and video."""
import asyncio
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from codex_wechat.app import Bridge
from codex_wechat.command_specs import REGISTRY
from codex_wechat.errors import WeChatApiError, WeChatAuthExpired, WeChatTransportError
from codex_wechat.media import Media, split_answer
from codex_wechat.records import (AcceptedUnknown, AuthenticationRequired, Delivered,
                                  Rejected, Retryable)
from codex_wechat.storage import Store
from codex_wechat.wechat import SEND_TIMEOUT, TYPING_TIMEOUT, WeChat
from codex_wechat.wechat_content import remember_quote


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
        bridge.channel.send = AsyncMock(return_value=Delivered("900"))
        self.tasks.append(asyncio.create_task(bridge.sender()))
        await asyncio.wait_for(bridge.outbox_drained.wait(), 1)
        self.assertEqual(bridge.state["quoteCache"]["900"], "answer")

    async def test_alternate_ack_id_is_cached_for_reply_quotes(self):
        bridge = Bridge(self.store, {"user": "owner", "token": "token"}, {}, channel=FakeChannel())
        bridge.state["context"] = "context"
        bridge.reply("answer")
        bridge.channel.send = AsyncMock(return_value=Delivered("900"))
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
        await asyncio.sleep(0)
        await bridge.typing.stop()
        self.assertEqual(channel.statuses, [("owner", "ticket", 1)])
        await bridge.typing.stop()
        self.assertEqual(channel.statuses,
                         [("owner", "ticket", 1), ("owner", "ticket", 2)])

    async def test_typing_ticket_cache_refreshes_at_expiry_and_identity_change(self):
        channel = FakeChannel()
        channel.typing_ticket = AsyncMock(side_effect=["first", "second", "third"])
        bridge = Bridge(self.store, {"user": "owner", "bot": "bot", "token": "token"}, {}, channel=channel)
        with patch("codex_wechat.typing_indicator.time.monotonic", return_value=100) as clock:
            self.assertEqual(await bridge.typing._get_ticket(), "first")
            clock.return_value = 699
            self.assertEqual(await bridge.typing._get_ticket(), "first")
            channel.typing_ticket.assert_awaited_once()
            clock.return_value = 700
            self.assertEqual(await bridge.typing._get_ticket(), "second")
            bridge.account["bot"] = "new-bot"
            self.assertEqual(await bridge.typing._get_ticket(), "third")
        self.assertEqual(channel.typing_ticket.await_count, 3)

    async def test_failed_refresh_does_not_keep_expired_ticket(self):
        channel = FakeChannel()
        channel.typing_ticket = AsyncMock(side_effect=RuntimeError("unavailable"))
        bridge = Bridge(self.store, {"user": "owner", "token": "token"}, {}, channel=channel)
        bridge.typing.ticket, bridge.typing.ticket_key = "old", (None, "owner")
        bridge.typing.ticket_expires = 0
        with self.assertRaises(RuntimeError):
            await bridge.typing._get_ticket()
        self.assertEqual(bridge.typing.ticket, "")
        self.assertEqual(bridge.typing.ticket_expires, 0)

    async def test_stop_refreshes_an_expired_ticket(self):
        channel = FakeChannel()
        channel.typing_ticket = AsyncMock(return_value="fresh")
        bridge = Bridge(self.store, {"user": "owner", "token": "token"}, {}, channel=channel)
        bridge.typing.active = 1
        bridge.typing.started = True
        bridge.typing.user = "owner"
        bridge.typing.ticket, bridge.typing.ticket_key = "old", (None, "owner")
        bridge.typing.ticket_expires = 0
        await bridge.typing.stop()
        self.assertEqual(channel.statuses, [("owner", "fresh", 2)])

    async def test_background_typing_auth_failure_pauses_bridge(self):
        channel = FakeChannel()
        channel.typing_ticket = AsyncMock(side_effect=WeChatAuthExpired())
        bridge = Bridge(self.store, {"user": "owner", "token": "token"}, {}, channel=channel)
        paused = asyncio.Event()
        async def pause():
            bridge.wechat_paused = True
            paused.set()
        bridge.require_wechat_rebind = AsyncMock(side_effect=pause)
        await bridge.typing.start("owner", "context")
        await asyncio.wait_for(paused.wait(), 1)
        await bridge.typing.stop()
        bridge.require_wechat_rebind.assert_awaited_once()
        self.assertTrue(bridge.wechat_paused)

    async def test_slow_typing_ticket_does_not_block_start_and_is_cancelled(self):
        channel = FakeChannel()
        entered, cancelled = asyncio.Event(), asyncio.Event()
        async def ticket(*args):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        channel.typing_ticket = ticket
        bridge = Bridge(self.store, {"user": "owner", "token": "token"}, {}, channel=channel)
        self.assertTrue(await asyncio.wait_for(bridge.typing.start("owner", "context"), 1))
        await asyncio.wait_for(entered.wait(), 1)
        task = bridge.typing.task
        await bridge.typing.stop()
        self.assertTrue(cancelled.is_set())
        self.assertTrue(task.done())
        self.assertEqual(channel.statuses, [])

    async def test_slow_typing_send_is_cancelled_before_stop_status(self):
        channel = FakeChannel()
        entered = asyncio.Event()
        async def status(user, ticket, value):
            channel.statuses.append((user, ticket, value))
            if value == 1:
                entered.set()
                await asyncio.Event().wait()
        channel.set_typing = status
        bridge = Bridge(self.store, {"user": "owner", "token": "token"}, {}, channel=channel)
        self.assertTrue(await asyncio.wait_for(bridge.typing.start("owner", "context"), 1))
        await asyncio.wait_for(entered.wait(), 1)
        await bridge.typing.stop()
        self.assertEqual([value for _, _, value in channel.statuses], [1, 2])

    async def test_keepalive_auth_failure_uses_the_same_pause(self):
        channel = FakeChannel()
        channel.set_typing = AsyncMock(side_effect=WeChatAuthExpired())
        bridge = Bridge(self.store, {"user": "owner", "token": "token"}, {}, channel=channel)
        bridge.require_wechat_rebind = AsyncMock()
        bridge.typing.user, bridge.typing.context = "owner", "context"
        with patch("codex_wechat.typing_indicator.KEEPALIVE_SECONDS", 0):
            await asyncio.wait_for(bridge.typing._keepalive(), 1)
        bridge.require_wechat_rebind.assert_awaited_once()
        self.assertEqual(bridge.typing.ticket, "")

    async def test_typing_api_contract(self):
        client = WeChat({"token": "token", "base_url": "https://ilinkai.weixin.qq.com"})
        client.request = AsyncMock(side_effect=[{"typing_ticket": "ticket"}, {}])
        self.assertEqual(await client.typing_ticket("owner", "context"), "ticket")
        await client.set_typing("owner", "ticket", 1)
        first, second = client.request.await_args_list
        self.assertEqual(first.args[0], "ilink/bot/getconfig")
        self.assertEqual(first.args[1],
                         {"ilink_user_id": "owner", "context_token": "context"})
        self.assertEqual(first.kwargs["timeout"], TYPING_TIMEOUT)
        self.assertEqual(second.args[0], "ilink/bot/sendtyping")
        self.assertEqual(second.args[1],
                         {"ilink_user_id": "owner", "typing_ticket": "ticket", "status": 1})
        self.assertEqual(second.kwargs["timeout"], TYPING_TIMEOUT)

    async def test_send_message_uses_short_timeout(self):
        client = WeChat({"token": "token", "base_url": "https://ilinkai.weixin.qq.com"})
        client.request = AsyncMock(return_value={"msgid": 900})
        self.assertEqual(await client.send({
            "id": "client", "user": "owner", "context": "context", "text": "answer",
        }), Delivered("900"))
        self.assertEqual(client.request.await_args.kwargs["timeout"], SEND_TIMEOUT)

    async def test_send_maps_failures_to_explicit_outcomes(self):
        client = WeChat({"token": "token", "base_url": "https://ilinkai.weixin.qq.com"})
        cases = (
            (WeChatAuthExpired(), AuthenticationRequired()),
            (WeChatApiError(-2, "prepare_failed"), Rejected(-2, "prepare_failed")),
            (WeChatTransportError("offline"), Retryable("WeChatTransportError")),
        )
        for error, expected in cases:
            with self.subTest(error=type(error).__name__):
                client.request = AsyncMock(side_effect=error)
                self.assertEqual(await client.send({
                    "id": "client", "user": "owner",
                    "context": "context", "text": "answer",
                }), expected)

    async def test_a_200_without_a_message_id_is_not_reported_as_sent(self):
        # Observed in the wild: the bot's outbound text channel was blocked, the
        # server answered {"ret": 0} and delivered nothing while sendtyping kept
        # working. Returning normally lost the reply silently.
        client = WeChat({"token": "token", "base_url": "https://ilinkai.weixin.qq.com"})
        client.request = AsyncMock(return_value={"ret": 0})
        self.assertEqual(await client.send({
            "id": "client", "user": "owner", "context": "context", "text": "answer",
        }), AcceptedUnknown(0))

    async def test_an_empty_response_is_not_reported_as_sent(self):
        client = WeChat({"token": "token", "base_url": "https://ilinkai.weixin.qq.com"})
        client.request = AsyncMock(return_value={})
        self.assertEqual(await client.send({
            "id": "client", "user": "owner", "context": "context", "text": "answer",
        }), AcceptedUnknown(None))

    async def test_empty_or_non_scalar_message_ids_are_not_acknowledgements(self):
        client = WeChat({"token": "token", "base_url": "https://ilinkai.weixin.qq.com"})
        for response in ({"ret": 0, "msgid": None},
                         {"ret": 0, "message_id": ""},
                         {"ret": 0, "messageId": "   "},
                         {"ret": 0, "msg_id": True},
                         {"ret": 0, "msgId": []},
                         {"ret": 0, "msg_id": {}}):
            with self.subTest(response=response):
                client.request = AsyncMock(return_value=response)
                self.assertEqual(await client.send({
                    "id": "client", "user": "owner",
                    "context": "context", "text": "answer",
                }), AcceptedUnknown(response.get("ret")))

    async def test_any_supported_protocol_id_is_accepted(self):
        client = WeChat({"token": "token", "base_url": "https://ilinkai.weixin.qq.com"})
        for response, expected in (({"msgid": 900}, "900"),
                                   ({"msg_id": "901"}, "901"),
                                   ({"message_id": "902"}, "902"),
                                   ({"messageId": "903"}, "903"),
                                   ({"msgId": "904"}, "904")):
            with self.subTest(response=response):
                client.request = AsyncMock(return_value=response)
                self.assertEqual(await client.send({
                    "id": "client", "user": "owner",
                    "context": "context", "text": "answer",
                }), Delivered(expected))

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
            with patch("codex_wechat.media.cipher", return_value=b"encrypted-video"):
                item = await media.upload({"asset": asset, "user": "owner"})
            self.assertEqual(item["type"], 5)
            self.assertIn("video_item", item)
            self.assertEqual(channel.request.await_args.args[1]["media_type"], 2)
        self.assertEqual(split_answer(
            'answer\nWECHAT_MEDIA: {"path":"/tmp/clip.mp4","kind":"video"}')[1],
            [{"path": "/tmp/clip.mp4", "kind": "video"}])
        self.assertIn("/getfile", REGISTRY)

    async def test_media_marker_in_mismatched_nested_fence_is_text(self):
        answer = (
            "~~~text\n"
            "```\n"
            'WECHAT_MEDIA: {"path":"/tmp/secret.txt"}\n'
            "```\n"
            "~~~"
        )
        text, attachments = split_answer(answer)
        self.assertEqual(text, answer)
        self.assertEqual(attachments, [])

    async def test_remember_quote_is_bounded(self):
        state = {}
        for number in range(205):
            remember_quote(state, number, f"message {number}")
        self.assertEqual(len(state["quoteCache"]), 200)
        self.assertEqual(next(iter(state["quoteCache"])), "5")
