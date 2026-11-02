"""Desktop capabilities through the common controller; no real App or backend."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import desktop
from codex_connection import ConnectionSpec


class DesktopEntryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.enterContext(patch.object(desktop, 'data_root', return_value=self.root))
        self.enterContext(patch.object(desktop, 'discover_app_codex', return_value=None))
        self.config = {'backends': {'codex': {'websocketUrl': 'ws://127.0.0.1:12345',
                                             'appCommand': ['app-native']}}}
        (self.root/'config.json').write_text(json.dumps(self.config))
        self.spec = ConnectionSpec.from_config(self.config['backends']['codex'])

    async def test_cli_preserves_arguments_directory_and_exit_status(self):
        cwd = os.getcwd()
        with patch.object(desktop, 'ensure', AsyncMock(return_value=(self.spec, {'PATH': ''}))), \
                patch.object(desktop.subprocess, 'call', return_value=7) as call:
            code = await desktop.main(['cli', '--', 'resume', '-C', 'space folder'])
        self.assertEqual(code, 7)
        self.assertEqual(call.call_args.args[0][-5:], ['--remote', self.spec.endpoint, 'resume', '-C', 'space folder'])
        self.assertEqual(os.getcwd(), cwd)

    async def test_app_connection_is_child_only_and_does_not_change_proxy(self):
        child_env = {'HTTPS_PROXY': 'http://proxy.invalid:9000'}
        original = dict(os.environ)
        with patch.object(desktop, 'ensure', AsyncMock(return_value=(self.spec, child_env))), \
                patch.object(desktop.subprocess, 'Popen') as spawn:
            self.assertEqual(await desktop.main(['app']), 0)
        self.assertEqual(spawn.call_args.kwargs['env']['CODEX_APP_SERVER_WS_URL'], self.spec.endpoint)
        self.assertEqual(spawn.call_args.kwargs['env']['HTTPS_PROXY'], child_env['HTTPS_PROXY'])
        self.assertEqual(dict(os.environ), original)

    async def test_app_requires_explicit_shared_setup(self):
        (self.root/'config.json').write_text(json.dumps({'backends': {'codex': {'appCommand': ['app-native']}}}))
        with patch.object(desktop, 'ensure', AsyncMock()) as ensure:
            with self.assertRaisesRegex(ValueError, 'setup'):
                await desktop.main(['app'])
        ensure.assert_not_awaited()

    async def test_no_implicit_override_of_cli_endpoint(self):
        for option in ('--remote=ws://example.com:80', '--no-daemon', '--remote-auth-token-env=TOKEN'):
            with self.subTest(option=option), self.assertRaises(ValueError):
                await desktop.main(['cli', '--', option])

    async def test_setup_preserves_hot_config_and_other_backends(self):
        initial = {'processingNotice': 'kept', 'backends': {'other': {'command': ['custom', 'app-server']}}}
        path = self.root/'config.json'
        path.write_text(json.dumps(initial))
        with patch.object(desktop, 'discover_codex', return_value='/native/codex'):
            await desktop.main(['setup', '--url', 'ws://127.0.0.1:12345'])
        saved = json.loads(path.read_text())
        self.assertEqual(saved['processingNotice'], 'kept')
        self.assertEqual(saved['backends']['other'], initial['backends']['other'])
        self.assertEqual(saved['backends']['codex']['websocketUrl'], self.spec.endpoint)
        self.assertIn('ensure', saved['backends']['codex']['websocketStartupCommand'])
