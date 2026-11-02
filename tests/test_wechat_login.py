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
        with self.assertRaisesRegex(RuntimeError, "--replace-identity"):
            await self.login(changed, before_identity_change=callback)
        self.assertEqual(self.store.load("account"), self.old)
        callback.assert_not_called()

    async def test_explicit_identity_change_calls_migration_before_save(self):
        changed = {"token": "new", "bot": "bot-b", "user": "user-b"}
        observed = []

        def before(previous, account):
            observed.append((self.store.load("account"), previous, account))

        result = await self.login(
            changed,
            allow_identity_change=True,
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
            allow_identity_change=True,
            before_identity_change=callback,
        )
        self.assertFalse(result.identity_changed)
        callback.assert_not_called()
        saved = self.store.load("account")
        self.assertEqual(saved["base_url"], wechat.BASE_URL)
        self.assertEqual({key: saved[key] for key in refreshed}, refreshed)

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
