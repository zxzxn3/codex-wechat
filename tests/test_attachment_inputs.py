"""Attachment inputs: native image items and minimal file references, no network."""
import json
import unittest
from unittest.mock import AsyncMock

from backend import CodexBackend


class AttachmentInputTests(unittest.IsolatedAsyncioTestCase):
    def backend(self, modalities=("text", "image")):
        backend = CodexBackend({}, "/tmp", lambda _: None)
        backend.info = {"model": "test"}
        backend.models = AsyncMock(return_value=[{"model": "test", "inputModalities": list(modalities)}])
        backend.input_hint = lambda: ""
        return backend

    async def test_image_only_has_native_item_without_synthetic_prompt(self):
        backend = self.backend()
        items = await backend.input_items("", [{"path": "/tmp/a.png", "kind": "image"}])
        self.assertEqual(items, [{"type": "localImage", "path": "/tmp/a.png"}])

    async def test_mixed_attachments_preserve_text_and_only_reference_files(self):
        backend = self.backend()
        file = {"path": "/tmp/data.json", "kind": "file"}
        items = await backend.input_items("look", [{"path": "/tmp/a.png", "kind": "image"}, file])
        self.assertEqual(items[0], {"type": "text", "text": "look"})
        self.assertEqual(items[1], {"type": "localImage", "path": "/tmp/a.png"})
        self.assertEqual(json.loads(items[2]["text"]), {"attachments": [file]})

    async def test_nonvisual_model_retains_image_reference_without_local_image(self):
        backend = self.backend(("text",))
        image = {"path": "/tmp/a.png", "kind": "image"}
        items = await backend.input_items("", [image])
        self.assertFalse(any(item["type"] == "localImage" for item in items))
        self.assertEqual(json.loads(items[-1]["text"]), {"attachments": [image]})

    async def test_file_only_does_not_query_models_and_hint_stays_last(self):
        backend = self.backend()
        backend.input_hint = lambda: "\n\n[User from WeChat.]"
        file = {"path": "/tmp/file.txt", "kind": "file"}
        items = await backend.input_items("", [file])
        backend.models.assert_not_awaited()
        self.assertEqual(json.loads(items[0]["text"]), {"attachments": [file]})
        self.assertEqual(items[-1]["text"], "\n\n[User from WeChat.]")
