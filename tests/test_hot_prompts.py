"""Hot copy overrides stay local to a bridge and never rewrite frozen output."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend import CodexBackend
from codex_wechat import Bridge
from command_specs import HELP, help_text
from configuration import ConfigLoader, TEXT_LIMIT_BYTES
from prompts import (DEFAULT_PROMPTS, command_approval, prompt_text,
                     validate_prompt_overrides)
from storage import Store


class PromptCatalogueTests(unittest.TestCase):
    def test_all_defaults_pass_the_same_validation_as_overrides(self):
        self.assertEqual(validate_prompt_overrides(dict(DEFAULT_PROMPTS), limit=TEXT_LIMIT_BYTES),
                         dict(DEFAULT_PROMPTS))

    def test_single_pass_preserves_unknown_fields_and_literal_braces(self):
        self.assertEqual(prompt_text("QUEUE_CONFIRMED", {"count": "{model}", "model": "wrong"},
            overrides={"QUEUE_CONFIRMED": "{{example}} {count} {unknown}"}), "{example} {model} {unknown}")

    def test_invalid_overrides_cannot_add_expressions_or_unbounded_formatting(self):
        cases = [[], {"UNKNOWN_NAME": "text"}, {"STOP_IDLE": None},
                 {"STOP_IDLE": "{"}, {"STOP_IDLE": "{model.name}"},
                 {"STOP_IDLE": "{model[0]}"}, {"STOP_IDLE": "{model!r}"},
                 {"STOP_IDLE": "{model:1000000000}"}, {"STOP_IDLE": "界" * 1001}]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_prompt_overrides(value, limit=TEXT_LIMIT_BYTES)

    def test_help_defaults_still_come_from_the_command_catalogue(self):
        self.assertEqual(help_text(), HELP)
        custom = help_text(text=lambda name: prompt_text(name, overrides={"HELP_STATUS": "Live status"}))
        self.assertIn('`/status` Live status `/s`', custom)
        self.assertIn('`/sessions {页码}`', custom)

    def test_approval_labels_do_not_replace_command_or_response_token(self):
        text = lambda name: prompt_text(name, overrides={"LABEL_COMMAND": "Command",
                                                       "ACTION_APPROVE": "Allow"})
        result = command_approval(command="echo {MODEL}", cwd="/tmp", reason=None,
                                  token="fixed-token", text=text)
        self.assertIn('Command: echo {MODEL}', result)
        self.assertIn('`/approve fixed-token` Allow', result)
        self.assertIn('`/deny fixed-token`', result)


class HotPromptTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.path = root / "config.json"
        self.config = {"prompts": {"STOP_IDLE": "idle {model}"}}
        self.write(self.config)
        self.store = Store(root / "state")
        self.bridge = Bridge(self.store, {"user": "owner"}, dict(self.config), config_path=self.path)
        self.bridge.state.update(context="context", thread="thread")
        self.bridge.backend.selected_thread = "thread"
        self.bridge.backend.info.update(model="model-x")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def write(self, value):
        # Atomic editor replacement also changes inode, independent of clock resolution.
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        temporary.replace(self.path)

    async def test_control_reply_reloads_and_uses_status_snapshot_without_rpc(self):
        with patch.object(self.bridge.backend, 'rpc', side_effect=AssertionError('unexpected RPC')):
            await self.bridge.control('/stop')
        self.assertEqual(self.bridge.state['outbox'][-1]['text'], 'idle model-x')
        self.write({'prompts': {'STOP_IDLE': 'new idle {model}'}})
        await self.bridge.control('/stop')
        self.assertEqual(self.bridge.state['outbox'][-1]['text'], 'new idle model-x')
        self.assertEqual(self.bridge.state['outbox'][0]['text'], 'idle model-x')

    async def test_queue_locals_override_shared_fields_and_default_returns_after_delete(self):
        self.write({'prompts': {'QUEUE_CONFIRMED': '{count} pending {model}'}})
        await self.bridge.receive({'msgs': [{'message_id': 'queue', 'from_user_id': 'owner',
            'message_type': 1, 'context_token': 'context',
            'item_list': [{'type': 1, 'text_item': {'text': '/q task'}}]}]})
        self.assertEqual(self.bridge.state['outbox'][-1]['text'], '1 pending model-x')
        self.write({})
        self.assertEqual(self.bridge.prompt('QUEUE_CONFIRMED', {'count': 2, 'unit': 'tasks'}),
                         'Queue 2 tasks')

    async def test_invalid_file_retains_the_whole_previous_hot_snapshot(self):
        self.bridge.state['processingNotice'] = 'wechat override'
        self.write({'processingNotice': 'new', 'sourceHint': 'changed',
                    'prompts': {'STOP_IDLE': '{model.__class__}'}})
        self.assertEqual(self.bridge.prompt('STOP_IDLE'), 'idle model-x')
        self.assertEqual(self.bridge.state['processingNotice'], 'wechat override')
        self.assertNotEqual(self.bridge.source_hint, 'changed')

    async def test_prompt_changes_do_not_clear_existing_notice_override(self):
        self.bridge.state['processingNotice'] = 'wechat override'
        self.write({'prompts': {'STOP_IDLE': 'updated'}})
        self.assertEqual(self.bridge.prompt('STOP_IDLE'), 'updated')
        self.assertEqual(self.bridge.state['processingNotice'], 'wechat override')

    async def test_backend_event_reads_new_copy_without_incoming_message(self):
        backend = self.bridge.backend
        self.write({'compactionNotice': True, 'prompts': {'COMPACT_STARTED': 'shrinking'}})
        backend.event({'method': 'item/started', 'params': {'threadId': 'thread',
            'turnId': 'turn', 'item': {'type': 'contextCompaction', 'id': 'compact'}}})
        self.assertEqual(self.bridge.state['outbox'][-1]['text'], 'shrinking')

    async def test_labels_change_while_status_keys_and_data_stay_stable(self):
        self.write({'prompts': {'LABEL_MODEL': 'Model'}})
        await self.bridge.control('/s')
        self.assertIn('Model: model-x', self.bridge.state['outbox'][-1]['text'])
        self.assertEqual(self.bridge.prompt_fields()['model'], 'model-x')

    async def test_agent_text_and_old_output_are_never_reinterpreted_as_templates(self):
        self.write({'prompts': {'STOP_IDLE': 'replacement'}})
        await self.bridge.deliver_answer(DEFAULT_PROMPTS['STOP_IDLE'])
        self.assertEqual(self.bridge.state['outbox'][-1]['text'], DEFAULT_PROMPTS['STOP_IDLE'])

    async def test_two_bridges_and_unattached_backends_have_independent_copy(self):
        other = Bridge(self.store, {'user': 'owner'}, {'prompts': {'STOP_IDLE': 'other'}})
        self.assertEqual(other.prompt('STOP_IDLE'), 'other')
        self.assertEqual(self.bridge.prompt('STOP_IDLE'), 'idle model-x')
        standalone = CodexBackend({}, '/tmp', lambda _: None)
        self.assertEqual(standalone.prompt('STOP_IDLE'), DEFAULT_PROMPTS['STOP_IDLE'])
