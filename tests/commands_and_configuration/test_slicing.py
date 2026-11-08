"""Offline text contracts; no model, channel, state directory or service."""
import random
import unittest

from codex_wechat.configuration import Slicing, TEXT_LIMIT_BYTES
from codex_wechat.slicing import split_text


class SlicingTests(unittest.TestCase):
    def bounded(self, parts):
        self.assertTrue(all(0 < len(part.encode()) <= TEXT_LIMIT_BYTES for part in parts))

    def test_controls_ignore_semantic_settings(self):
        text = "# 状态\n\n" + ("一行模型清单\n" * 100)
        self.assertEqual(split_text(text, Slicing(target_chars=80)), [text])

    def test_short_answer_and_whitespace_are_unchanged(self):
        text = "可以。\n\n配置已经更新。\n"
        self.assertEqual(split_text(text, semantic=True), [text])
        self.assertEqual(split_text("", semantic=True), [])

    def test_smart_splits_under_transport_limit_without_losing_content(self):
        text = "\n\n".join("主题" + str(i) + "。" * 150 for i in range(5))
        parts = split_text(text, Slicing(target_chars=200), semantic=True)
        self.assertGreater(len(parts), 1)
        self.assertEqual("".join(parts), text)
        self.bounded(parts)

    def test_heading_stays_with_following_paragraph(self):
        text = "前言" * 90 + "\n\n# 原因\n\n" + "说明" * 60
        parts = split_text(text, Slicing(target_chars=200), semantic=True)
        self.assertEqual("".join(parts), text)
        self.assertTrue(any("# 原因\n\n说明" in part for part in parts))
        self.assertFalse(any(part.rstrip().endswith("# 原因") for part in parts))

    def test_list_and_table_that_fit_are_not_split(self):
        blocks = ["- 第一项\n- 第二项\n- 第三项\n", "| A | B |\n|---|---|\n| x | y |\n"]
        for block in blocks:
            text = "前言" * 80 + "\n\n" + block
            parts = split_text(text, Slicing(target_chars=80), semantic=True)
            self.assertTrue(any(block in part for part in parts))

    def test_complete_fence_preserved_and_long_fence_reopened(self):
        short = "```python\na = 1\n\nprint(a)\n```\n"
        self.assertEqual(split_text(short, semantic=True), [short])
        body = "变量 = '汉字😀'\n" * 400
        parts = split_text("```python\n" + body + "```", semantic=True)
        self.bounded(parts)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(part.startswith("```python\n") and part.endswith("```") for part in parts))
        self.assertEqual("".join(part[len("```python\n"):-len("\n```")] for part in parts), body)

    def test_length_mode_is_lossless_with_multibyte_and_long_tokens(self):
        text = "\n" + "😀汉abc" * 2000 + "\nend"
        for semantic in (False, True):
            parts = split_text(text, Slicing(mode="length"), semantic=semantic)
            self.assertEqual("".join(parts), text)
            self.bounded(parts)

    def test_smart_boolean_maps_to_mode(self):
        self.assertEqual(Slicing.from_config({"smart": True}).mode, "smart")
        self.assertEqual(Slicing.from_config({"smart": False}).mode, "length")
        with self.assertRaises(ValueError):
            Slicing.from_config({"smart": "false"})

    def test_config_rejects_ambiguous_or_invalid_values(self):
        for value in ({"mode": "smart"}, {"maxParts": 3}, {"targetChars": True},
                      {"targetChars": 0}, [], {"targetChars": "600"}):
            with self.assertRaises(ValueError):
                Slicing.from_config(value)

    def test_generated_unicode_texts_round_trip_at_byte_boundaries(self):
        rng = random.Random(20261109)
        alphabet = ("a", "汉", "😀", "\n", "\r\n", " ", "\t", "```\n", "~~~\n")
        for _ in range(80):
            text = "".join(rng.choice(alphabet) for _ in range(rng.randrange(0, 350)))
            settings = Slicing(
                mode="smart" if rng.choice((False, True)) else "length",
                target_chars=rng.randrange(80, 500),
            )
            parts = split_text(text, settings, semantic=True)
            with self.subTest(text=repr(text[:80]), mode=settings.mode):
                self.assertEqual("".join(parts), text)
                self.bounded(parts)
