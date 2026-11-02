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
                patch.object(ConnectionSpec, 'resolve', return_value=['fake-codex']), \
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

    async def test_native_app_uses_existing_daemon_and_forwards_urls(self):
        (self.root/'config.json').write_text(json.dumps({'backends': {'codex': {'appCommand': ['app-native']}}}))
        native = ConnectionSpec.from_config({'sharedDaemon': True})
        env = {'HTTPS_PROXY': 'http://proxy.invalid:9000', 'CODEX_APP_SERVER_WS_URL': 'stale'}
        with patch.object(desktop, 'IS_WINDOWS', False), \
                patch.object(desktop, 'discover_app_codex', return_value='/app/codex'), \
                patch.object(desktop, 'ensure', AsyncMock(return_value=(native, env))), \
                patch.object(desktop, 'run', AsyncMock(return_value=b'{"appServerVersion":"0.166.0"}')) as probe, \
                patch.object(desktop.subprocess, 'Popen') as spawn:
            self.assertEqual(await desktop.main(['app', '--', 'codex://thread/one']), 0)
        child = spawn.call_args.kwargs['env']
        self.assertEqual(child['CODEX_APP_SERVER_USE_LOCAL_DAEMON'], '1')
        self.assertEqual(child['HTTPS_PROXY'], env['HTTPS_PROXY'])
        self.assertNotIn('CODEX_APP_SERVER_WS_URL', child)
        self.assertEqual(env['CODEX_APP_SERVER_WS_URL'], 'stale')
        self.assertEqual(spawn.call_args.args[0], ['app-native', 'codex://thread/one'])
        self.assertEqual(probe.call_args.args[0], ['/app/codex', 'app-server', 'daemon', 'version'])

    async def test_windows_app_still_requires_websocket(self):
        (self.root/'config.json').write_text(json.dumps({'backends': {'codex': {'appCommand': ['app-native']}}}))
        with patch.object(desktop, 'IS_WINDOWS', True), patch.object(desktop, 'ensure', AsyncMock()) as ensure:
            with self.assertRaisesRegex(ValueError, 'setup'):
                await desktop.main(['app'])
        ensure.assert_not_awaited()

    async def test_native_app_rejects_overrides_before_starting_backend(self):
        (self.root/'config.json').write_text(json.dumps({'backends': {'codex': {'appCommand': ['app-native']}}}))
        for setting in ({'CODEX_APP_SERVER_FORCE_CLI': '1'}, {'CODEX_CLI_PATH': '/other/codex'}):
            with self.subTest(setting=setting), patch.dict(os.environ, setting), \
                    patch.object(desktop, 'IS_WINDOWS', False), \
                    patch.object(desktop, 'ensure', AsyncMock()) as ensure:
                with self.assertRaisesRegex(ValueError, '独立后台'):
                    await desktop.main(['app'])
                ensure.assert_not_awaited()

    async def test_native_app_does_not_spawn_without_running_daemon(self):
        (self.root/'config.json').write_text(json.dumps({'backends': {'codex': {'appCommand': ['app-native']}}}))
        native = ConnectionSpec.from_config({'sharedDaemon': True})
        with patch.object(desktop, 'IS_WINDOWS', False), \
                patch.object(desktop, 'discover_app_codex', return_value='/app/codex'), \
                patch.object(desktop, 'ensure', AsyncMock(return_value=(native, {}))), \
                patch.object(desktop, 'run', AsyncMock(return_value=b'{"appServerVersion":null}')), \
                patch.object(desktop.subprocess, 'Popen') as spawn:
            with self.assertRaisesRegex(ValueError, '未发现'):
                await desktop.main(['app'])
        spawn.assert_not_called()

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

    async def test_shortcut_preserves_metadata_and_rejects_foreign_override(self):
        system = self.root/'system/applications'
        system.mkdir(parents=True)
        (system/desktop.APP_DESKTOP_ID).write_text(
            '[Desktop Entry]\nName=ChatGPT\nExec=chatgpt %U\nIcon=chatgpt\n'
            'Type=Application\nMimeType=x-scheme-handler/codex;\nDBusActivatable=true\n')
        user = self.root/'user'
        env = {'XDG_DATA_HOME': str(user), 'XDG_DATA_DIRS': str(system.parent)}
        with patch.dict(os.environ, env), patch.object(desktop, 'IS_WINDOWS', False):
            await desktop.main(['setup', '--native', '--shortcut'])
            target = user/'applications'/desktop.APP_DESKTOP_ID
            text = target.read_text()
            self.assertIn('Name=ChatGPT', text)
            self.assertIn('MimeType=x-scheme-handler/codex;', text)
            self.assertIn('DBusActivatable=false', text)
            self.assertIn('"app" "--backend" "codex" "--" %U', text)
            await desktop.main(['setup', '--native', '--shortcut'])
            target.write_text(text + '\n[Desktop Action custom]\nName=custom\nExec=chatgpt\n')
            custom = target.read_bytes()
            with self.assertRaisesRegex(ValueError, '额外动作'):
                await desktop.main(['setup', '--native', '--shortcut'])
            self.assertEqual(target.read_bytes(), custom)
            target.write_text('[Desktop Entry]\nName=custom\n')
            before = (self.root/'config.json').read_bytes()
            with self.assertRaisesRegex(ValueError, '自定义'):
                await desktop.main(['setup', '--native', '--shortcut'])
            self.assertEqual((self.root/'config.json').read_bytes(), before)
            self.assertEqual(target.read_text(), '[Desktop Entry]\nName=custom\n')

    def test_app_daemon_version_boundary(self):
        for version in ('0.140.0', '0.141.0-alpha.1', 'unknown', None):
            with self.subTest(version=version):
                self.assertFalse(desktop.supports_app_daemon(version))
        for version in ('0.141.0', '0.141.0+build.1', '0.166.0-alpha.5.1', '0.166.0'):
            with self.subTest(version=version):
                self.assertTrue(desktop.supports_app_daemon(version))

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
