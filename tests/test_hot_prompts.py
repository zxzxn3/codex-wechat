"""Hot copy overrides stay local to a bridge and never rewrite frozen output."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend import CodexBackend
from codex_wechat import Bridge
from command_specs import HELP, HELP_ALL, help_text
from configuration import TEXT_LIMIT_BYTES
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
        self.assertEqual(help_text(show_all=True), HELP_ALL)
        custom = help_text(text=lambda name: prompt_text(name, overrides={"HELP_STATUS": "Live status"}))
        self.assertIn('`/status` Live status `/s`', custom)
        self.assertIn('`/sessions [页码|搜索词]`', custom)
        for command in ("/approve", "/deny", "/answer", "/get", "/getfile",
                        "/cwd", "/switch", "/ping"):
            self.assertNotIn(f"`{command} ", HELP)
            self.assertNotIn(f"`{command}`", HELP)
            self.assertTrue(f"`{command} " in HELP_ALL or f"`{command}`" in HELP_ALL)

    def test_approval_labels_do_not_replace_command_or_response_token(self):
        def text(name):
            return prompt_text(name, overrides={"LABEL_COMMAND": "Command",
                                                "ACTION_APPROVE": "Allow"})
        result = command_approval(command="echo {MODEL}", cwd="/tmp", reason=None,
                                  token="fixed-token", text=text)
        self.assertIn('Command: echo {MODEL}', result)
        self.assertIn('`/approve fixed-token` Allow', result)
        self.assertIn('`/deny fixed-token`', result)


class HotPromptTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config_log = self.enterContext(patch("configuration.log_event"))
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
        self.assertEqual(self.bridge.prompt('QUEUE_CONFIRMED', {'count': 2, 'unit': '条'}),
                         '已排队: 2 条')

    async def test_invalid_file_retains_the_whole_previous_hot_snapshot(self):
        self.bridge.state['processingNotice'] = 'wechat override'
        self.write({'processingNotice': 'new', 'sourceHint': 'changed',
                    'prompts': {'STOP_IDLE': '{model.__class__}'}})
        self.assertEqual(self.bridge.prompt('STOP_IDLE'), 'idle model-x')
        self.assertEqual(self.bridge.state['processingNotice'], 'wechat override')
        self.assertNotEqual(self.bridge.source_hint, 'changed')

    async def test_invalid_config_notifies_and_logs_once_without_exposing_content(self):
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text('{"sourceHint":"PRIVATE-SENTINEL",', encoding='utf-8')
        temporary.replace(self.path)
        for _ in range(3):
            self.assertEqual(self.bridge.prompt('STOP_IDLE'), 'idle model-x')
        notices = self.bridge.state['outbox']
        self.assertEqual([entry['text'] for entry in notices],
                         ['热配置未生效，保留原设置。请检查配置文件。'])
        self.config_log.assert_called_once_with('config.reload_failed', level='warning',
            component='config', kind='json', error_type='JSONDecodeError')
        self.assertNotIn('PRIVATE-SENTINEL', str(self.config_log.call_args))
        self.assertEqual(self.store.load('bridge')['outbox'], notices)

    async def test_fixed_config_applies_and_later_failure_can_notify_again(self):
        self.write({'slicing': {'targetChars': 1}})
        self.bridge.reload_config()
        self.write({'sourceHint': 'fixed'})
        self.bridge.reload_config()
        self.assertEqual(self.bridge.source_hint, 'fixed')
        self.assertFalse(self.bridge.settings.failure_pending)
        self.write({'slicing': {'targetChars': 1}})
        self.bridge.reload_config()
        self.assertEqual(len(self.bridge.state['outbox']), 2)
        self.assertEqual(self.config_log.call_count, 2)
        self.assertEqual(self.bridge.source_hint, 'fixed')

    async def test_config_error_notice_uses_last_valid_copy_without_recursive_reload(self):
        self.write({'prompts': {'CONFIG_RELOAD_FAILED': 'config rejected {model}'}})
        self.bridge.reload_config()
        self.write({'prompts': {'CONFIG_RELOAD_FAILED': '{model.__class__}'}})
        with patch.object(self.bridge.backend, 'rpc', side_effect=AssertionError('unexpected RPC')):
            self.bridge.reload_config()
        self.assertEqual(self.bridge.state['outbox'][-1]['text'], 'config rejected model-x')
        self.config_log.assert_called_once()

    async def test_config_notice_waits_for_chat_context(self):
        self.bridge.state.pop('context')
        self.write({'slicing': {'targetChars': 1}})
        self.bridge.reload_config()
        self.assertEqual(self.bridge.state['outbox'], [])
        self.assertTrue(self.bridge.settings.failure_pending)
        self.bridge.state['context'] = 'new-context'
        self.bridge.reload_config()
        self.assertEqual(len(self.bridge.state['outbox']), 1)
        self.assertFalse(self.bridge.settings.failure_pending)

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

    async def test_compaction_unconfirmed_fallback_reads_hot_copy(self):
        backend = self.bridge.backend
        self.write({'compactionNotice': True,
                    'prompts': {'COMPACT_UNCONFIRMED': 'result still unknown'}})
        backend.event({'method': 'item/started', 'params': {'threadId': 'thread',
            'turnId': 'turn', 'item': {'type': 'contextCompaction', 'id': 'compact'}}})
        backend.event({'method': 'turn/completed', 'params': {'threadId': 'thread',
            'turn': {'id': 'turn', 'status': 'completed'}}})
        self.assertEqual(self.bridge.state['outbox'][-1]['text'], 'result still unknown')

    async def test_missing_approval_command_reads_hot_copy(self):
        backend = self.bridge.backend
        backend.selected_thread = "thread"
        backend.active = backend.new_turn("thread", "turn", owned=True)
        notifications = []
        backend.notify = notifications.append
        self.write({'prompts': {'COMMAND_NOT_PROVIDED': 'not provided'}})
        await backend.server_request({
            "id": 7,
            "method": "item/commandExecution/requestApproval",
            "params": {"threadId": "thread", "turnId": "turn", "cwd": "/tmp"},
        })
        self.assertIn("命令: not provided", notifications[-1])

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
