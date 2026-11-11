"""Desktop capabilities through the common controller; no real App or backend."""
import builtins
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from codex_wechat import desktop
from codex_wechat.codex_connection import ConnectionSpec
from tests.support.helpers import runtime_paths_at


class DesktopEntryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.enterContext(patch.object(
            desktop, 'runtime_paths', return_value=runtime_paths_at(self.root)))
        self.enterContext(patch.object(desktop, 'discover_app_codex', return_value=None))
        self.config = {'backends': {'codex': {'websocketUrl': 'ws://127.0.0.1:12345',
                                             'runtime': 'app'}}}
        (self.root/'config.json').write_text(json.dumps(self.config))
        self.spec = ConnectionSpec.from_config(self.config['backends']['codex'])

    async def test_cli_preserves_arguments_directory_and_exit_status(self):
        cwd = os.getcwd()
        with patch.object(desktop, 'ensure', AsyncMock(return_value=(self.spec, {'PATH': ''}))), \
                patch.object(ConnectionSpec, 'resolve', return_value=['fake-codex']), \
                patch.object(desktop.subprocess, 'call', return_value=7) as call:
            code = await desktop.main(['cli', '--', 'resume', '-C', 'space folder'])
        self.assertEqual(code, 7)
        self.assertEqual(call.call_args.args[0][-5:], ['--remote', self.spec.endpoint, 'resume', '-C', 'space folder'])
        self.assertEqual(os.getcwd(), cwd)


    async def test_removed_app_and_shortcut_do_not_prepare_backend(self):
        before = (self.root/'config.json').read_bytes()
        for arguments in (['app'], ['setup', '--shortcut']):
            with self.subTest(arguments=arguments), patch.object(desktop, 'ensure', AsyncMock()) as ensure:
                with self.assertRaises(SystemExit):
                    await desktop.main(arguments)
                ensure.assert_not_awaited()
        self.assertEqual((self.root/'config.json').read_bytes(), before)

    async def test_native_setup_removes_explicit_websocket_only_when_requested(self):
        self.config['completionNotice'] = 'kept'
        self.config['backends']['codex']['websocketStartupCommand'] = ['old']
        path = self.root/'config.json'
        path.write_text(json.dumps(self.config))
        with patch.object(desktop, 'IS_WINDOWS', False):
            await desktop.main(['setup', '--native'])
        saved = json.loads(path.read_text())
        self.assertNotIn('websocketUrl', saved['backends']['codex'])
        self.assertNotIn('websocketStartupCommand', saved['backends']['codex'])
        self.assertTrue(saved['backends']['codex']['sharedDaemon'])
        self.assertEqual(saved['completionNotice'], 'kept')


    async def test_no_implicit_override_of_cli_endpoint(self):
        for option in ('--remote=ws://example.com:80', '--no-daemon', '--remote-auth-token-env=TOKEN'):
            with self.subTest(option=option), self.assertRaises(ValueError):
                await desktop.main(['cli', '--', option])

    async def test_setup_preserves_hot_config_and_other_backends(self):
        initial = {'processingNotice': 'kept', 'backends': {
            'codex': {}, 'other': {'command': ['custom', 'app-server']}}}
        path = self.root/'config.json'
        path.write_text(json.dumps(initial))
        with patch.object(desktop, 'discover_codex', return_value='/native/codex'):
            await desktop.main(['setup', '--url', 'ws://127.0.0.1:12345'])
        saved = json.loads(path.read_text())
        self.assertEqual(saved['processingNotice'], 'kept')
        self.assertEqual(saved['backends']['other'], initial['backends']['other'])
        self.assertEqual(saved['backends']['codex']['websocketUrl'], self.spec.endpoint)
        self.assertIn('ensure', saved['backends']['codex']['websocketStartupCommand'])

    async def test_setup_does_not_require_the_standalone_installer(self):
        real_import = builtins.__import__

        def reject_installer(name, *args, **kwargs):
            if name == "installer":
                raise ModuleNotFoundError(name)
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=reject_installer):
            await desktop.main(['setup', '--native'])
        saved = json.loads((self.root/'config.json').read_text())
        self.assertTrue(saved['backends']['codex']['sharedDaemon'])

    async def test_setup_reports_configuration_lock_contention(self):
        before = (self.root/'config.json').read_bytes()
        with patch.object(desktop, "locked_file",
                          side_effect=ValueError("另一个安装器正在修改该目录")):
            with self.assertRaisesRegex(ValueError, "另一个安装器"):
                await desktop.main(['setup', '--native'])
        self.assertEqual((self.root/'config.json').read_bytes(), before)
