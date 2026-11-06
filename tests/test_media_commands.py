"""The /get acknowledgement must not queue behind a slow upload."""
import unittest
from types import SimpleNamespace

from codex_wechat.commands import command_get
from codex_wechat.media import MediaError
from codex_wechat.prompts import SEND_FILE_FAILED


def prompt(name, values=None):
    if name == "ATTACHMENT_QUEUED":
        return "附件已排队"
    return SEND_FILE_FAILED.format(**values)


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
        self.assertIn("/tmp/missing.txt", calls[1][1])

    async def test_a_missing_windows_path_reports_the_typo_not_a_generic_error(self):
        # resolve(strict=True) raises a localized OSError whose text carries no
        # "不存在"; without the WinError wording the user only saw "附件处理失败".
        calls = []
        async def prepare_asset(path, kind="auto", *, origin=None):
            calls.append(("prepare", path, kind))
            raise FileNotFoundError(
                2, "系统找不到指定的文件。", r"C:\Temp\wechat-tst.txt")
        failing = bridge(calls)
        failing.prepare_asset = prepare_asset
        await command_get(failing, "/get", r"C:\Temp\wechat-tst.txt")
        self.assertIn("文件不存在或不可读", calls[1][1])
        self.assertIn(r"C:\Temp\wechat-tst.txt", calls[1][1])

    async def test_the_echoed_path_is_bounded(self):
        calls = []
        value = "/tmp/" + "a" * 400
        await command_get(bridge(calls), "/get", value)
        block = calls[1][1].split("```")[1].strip()
        self.assertEqual(len(block), 300)

    async def test_the_echoed_path_sits_in_its_own_code_block(self):
        # WeChat renders a copy button on fenced blocks, so the path must be the
        # whole block content rather than trailing text.
        calls = []
        await command_get(bridge(calls), "/get", "/tmp/missing.txt")
        self.assertIn("```\n/tmp/missing.txt\n```", calls[1][1])

    async def test_the_echoed_path_cannot_break_out_of_the_block(self):
        calls = []
        await command_get(bridge(calls), "/get", "a```b\nc.txt")
        self.assertEqual(calls[1][1].count("```"), 2)
        self.assertNotIn("\n", calls[1][1].split("```")[1].strip())

    async def test_getfile_forces_the_file_kind(self):
        calls = []
        await command_get(bridge(calls, snapshot={}), "/getfile", "/tmp/a.csv")
        self.assertEqual(calls[0], ("prepare", "/tmp/a.csv", "file"))
