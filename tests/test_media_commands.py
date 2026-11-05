"""The /get acknowledgement must not queue behind a slow upload."""
import unittest
from types import SimpleNamespace

from commands import command_get
from media import MediaError


def prompt(name, values=None):
    if name == "ATTACHMENT_QUEUED":
        return "附件已排队"
    return f"发送失败:{values['reason']}"


def bridge(calls, *, snapshot=None):
    async def prepare_asset(path, kind="auto", *, origin=None):
        calls.append(("prepare", path, kind))
        if snapshot is not None:
            return snapshot
        raise MediaError("附件不存在或无法读取。")

    def reply(text):
        calls.append(("reply", text))

    def queue_asset(asset, *, origin=None):
        calls.append(("asset", asset))

    return SimpleNamespace(prepare_asset=prepare_asset, reply=reply,
                           queue_asset=queue_asset, prompt=prompt)


class GetOrderingTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_receipt_is_queued_before_the_attachment(self):
        calls = []
        asset = {"path": "/tmp/a.txt", "name": "a.txt", "kind": "file"}
        await command_get(bridge(calls, snapshot=asset), "/get", "/tmp/a b.txt")
        self.assertEqual([call[0] for call in calls], ["prepare", "reply", "asset"])
        self.assertEqual(calls[0][1], "/tmp/a b.txt")
        self.assertEqual(calls[1][1], "附件已排队")
        self.assertEqual(calls[2][1], asset)

    async def test_a_local_failure_answers_without_a_receipt(self):
        calls = []
        await command_get(bridge(calls), "/get", "/tmp/missing.txt")
        self.assertEqual([call[0] for call in calls], ["prepare", "reply"])
        self.assertIn("文件不存在", calls[1][1])

    async def test_getfile_forces_the_file_kind(self):
        calls = []
        await command_get(bridge(calls, snapshot={}), "/getfile", "/tmp/a.csv")
        self.assertEqual(calls[0], ("prepare", "/tmp/a.csv", "file"))
