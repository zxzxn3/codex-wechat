"""Terminal QR backend selection without contacting WeChat."""
import io
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import wechat


class FakeQRCode:
    def __init__(self, value):
        self.value = value

    def add_data(self, value):
        self.value = value

    def print_ascii(self, *, invert):
        self.value = "rendered:" + self.value


class QRCodeTests(unittest.TestCase):
    def test_qrencode_is_preferred_and_receives_url_on_stdin(self):
        with patch.object(wechat.shutil, "which", return_value="/usr/bin/qrencode"), \
                patch.object(wechat.subprocess, "run") as run:
            wechat.print_qr("https://example.invalid/qr")
        args, kwargs = run.call_args
        self.assertEqual(args[0], [
            "/usr/bin/qrencode", "-t", "ANSIUTF8", "-o", "-",
        ])
        self.assertEqual(kwargs["input"], b"https://example.invalid/qr")
        self.assertTrue(kwargs["check"])

    def test_python_qrcode_is_used_when_qrencode_fails(self):
        rendered = SimpleNamespace(value=None)

        def make_code(*, border):
            code = FakeQRCode(None)
            rendered.value = code
            return code

        fake = SimpleNamespace(QRCode=make_code)
        with patch.object(wechat.shutil, "which", return_value="/usr/bin/qrencode"), \
                patch.object(wechat.subprocess, "run",
                             side_effect=subprocess.CalledProcessError(1, "qrencode")), \
                patch.dict(sys.modules, {"qrcode": fake}):
            wechat.print_qr("https://example.invalid/qr")
        self.assertEqual(rendered.value.value, "rendered:https://example.invalid/qr")

    def test_link_fallback_when_no_renderer_is_available(self):
        output = io.StringIO()
        with patch.object(wechat.shutil, "which", return_value=None), \
                patch.dict(sys.modules, {"qrcode": None}), \
                patch("sys.stdout", output):
            wechat.print_qr("https://example.invalid/qr")
        self.assertIn("可使用二维码工具", output.getvalue())


if __name__ == "__main__":
    unittest.main()
