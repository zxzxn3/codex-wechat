"""Interactive login identity policy without contacting the WeChat API."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import wechat
from storage import Store


class FakeWeChat:
    def __init__(self, account, status="confirmed"):
        self.account = account
        self.status = status

    async def request(self, endpoint, body=None, *, auth=True, timeout=40):
        if endpoint.startswith("ilink/bot/get_bot_qrcode"):
            return {"qrcode": "fixture", "qrcode_img_content": "https://example.invalid"}
        if endpoint.startswith("ilink/bot/get_qrcode_status"):
            if self.status == "binded_redirect":
                return {"status": self.status}
            return {
                "status": "confirmed",
                "bot_token": self.account["token"],
                "ilink_user_id": self.account["user"],
                "ilink_bot_id": self.account["bot"],
                "baseurl": "https://ilinkai.weixin.qq.com",
            }
        raise AssertionError(endpoint)


class WeChatLoginPolicyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "state")
        self.old = {"token": "old", "bot": "bot-a", "user": "user-a"}
        self.store.save("account", self.old)

    def tearDown(self):
        self.store.lock.close()
        self.temp.cleanup()

    async def login(self, account, *, status="confirmed", **kwargs):
        client = FakeWeChat(account, status)
        with patch.object(wechat, "WeChat", return_value=client), \
                patch.object(wechat, "print_qr"):
            return await wechat.login(self.store, **kwargs)

    async def test_identity_change_is_refused_before_replacing_credentials(self):
        changed = {"token": "new", "bot": "bot-b", "user": "user-b"}
        callback = Mock()
        with self.assertRaisesRegex(RuntimeError, "rebind"):
            await self.login(changed, before_identity_change=callback)
        self.assertEqual(self.store.load("account"), self.old)
        callback.assert_not_called()

    async def test_explicit_identity_change_calls_migration_before_save(self):
        changed = {"token": "new", "bot": "bot-b", "user": "user-a"}
        observed = []

        def before(previous, account):
            observed.append((self.store.load("account"), previous, account))

        result = await self.login(
            changed,
            rebind=True,
            before_identity_change=before,
        )
        self.assertTrue(result.identity_changed)
        self.assertEqual(observed[0][0], self.old)
        self.assertEqual(observed[0][1], self.old)
        saved = self.store.load("account")
        self.assertEqual(saved["base_url"], wechat.BASE_URL)
        self.assertEqual({key: saved[key] for key in changed}, changed)

    async def test_same_identity_refreshes_token_without_migration(self):
        refreshed = {**self.old, "token": "new"}
        callback = Mock()
        result = await self.login(
            refreshed,
            rebind=True,
            before_identity_change=callback,
        )
        self.assertFalse(result.identity_changed)
        callback.assert_not_called()
        saved = self.store.load("account")
        self.assertEqual(saved["base_url"], wechat.BASE_URL)
        self.assertEqual({key: saved[key] for key in refreshed}, refreshed)

    async def test_same_user_bot_change_needs_no_prompt_or_extra_flag(self):
        changed = {**self.old, "token": "new", "bot": "bot-b"}
        before = Mock()
        with patch("builtins.input", side_effect=AssertionError("unexpected prompt")):
            result = await self.login(changed, rebind=True, before_identity_change=before)
        self.assertTrue(result.identity_changed)
        before.assert_called_once()
        self.assertEqual(self.store.load("account")["bot"], "bot-b")

    async def test_different_user_requires_terminal_confirmation_before_any_change(self):
        changed = {"token": "new", "bot": "bot-b", "user": "user-b"}
        before = Mock()
        with patch("builtins.input", return_value="n") as confirm:
            with self.assertRaisesRegex(RuntimeError, "已取消"):
                await self.login(changed, rebind=True, before_identity_change=before)
        confirm.assert_called_once()
        before.assert_not_called()
        self.assertEqual(self.store.load("account"), self.old)
        with patch("builtins.input", return_value="y"):
            result = await self.login(changed, rebind=True, before_identity_change=before)
        self.assertTrue(result.identity_changed)
        before.assert_called_once()
        self.assertEqual(self.store.load("account")["user"], "user-b")

    async def test_missing_terminal_input_cannot_accept_another_user(self):
        changed = {"token": "new", "bot": "bot-b", "user": "user-b"}
        with patch("builtins.input", side_effect=EOFError()):
            with self.assertRaisesRegex(RuntimeError, "已取消"):
                await self.login(changed, rebind=True, before_identity_change=Mock())
        self.assertEqual(self.store.load("account"), self.old)

    async def test_binded_redirect_reuses_existing_local_credentials(self):
        callback = Mock()
        result = await self.login(
            self.old,
            status="binded_redirect",
            before_identity_change=callback,
        )
        self.assertTrue(result.already_connected)
        self.assertFalse(result.identity_changed)
        self.assertEqual(result.account, self.old)
        callback.assert_not_called()
        self.assertEqual(self.store.load("account"), self.old)


if __name__ == "__main__":
    unittest.main()
