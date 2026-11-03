"""Cross-layer capabilities rather than source layout assertions."""
import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from backend import CodexBackend
from codex_connection import ConnectionSpec
from codex_wechat import Bridge, startup_account
from configuration import ConfigurationError
from records import InputPhase
from storage import Store
import rebind
import wechat_auth


class ConnectionCapabilities(unittest.TestCase):
    def test_caller_config_is_not_mutated_and_explicit_stdio_is_preserved(self):
        command = ['custom', 'app-server']
        config = {'command': command, 'sharedDaemon': True}
        spec = ConnectionSpec.from_config(config)
        self.assertEqual(spec.proxy_command(), ['custom', 'app-server', 'proxy'])
        self.assertEqual(command, ['custom', 'app-server'])
        self.assertFalse(ConnectionSpec.from_config({'sharedDaemon': False}).shared)

    def test_ambiguous_or_remote_endpoints_are_not_accepted(self):
        for endpoint in ('ws://127.0.0.1:0', 'ws://localhost:65536', 'ws://127.0.0.1:5/a',
                         'ws://127.0.0.1:5#x', 'ws://user@localhost:5', 'ws://external:5'):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                ConnectionSpec.from_config({'websocketUrl': endpoint})

    def test_queue_alone_does_not_require_forced_rebind(self):
        self.assertFalse(rebind.active_work({'inbox': [{'text': 'later'}]}))
        self.assertTrue(rebind.active_work({'currentInput': {'phase': 'accepted'}}))


class RuntimeCapabilities(unittest.IsolatedAsyncioTestCase):
    async def test_websocket_can_read_account_limits_without_a_child_process(self):
        backend = CodexBackend({'websocketUrl': 'ws://127.0.0.1:12345'}, '.', lambda _: None)
        backend.transport.websocket = object()
        backend.transport.reader = asyncio.get_running_loop().create_future()
        response = {'rateLimits': {'primary': {'usedPercent': 75, 'resetsAt': 123}}}
        backend.rpc = AsyncMock(return_value=response)
        self.assertIsNone(backend.proc)
        self.assertEqual(await backend.rate_limits(), response)
        backend.rpc.assert_awaited_once_with('account/rateLimits/read', {})
        backend.transport.reader.cancel()

    async def test_auth_expiry_is_a_dispatch_barrier_and_a_durable_single_notice(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp)/'state')
            try:
                bridge = Bridge(store, {'bot': 'b', 'user': 'u', 'token': 'fixture'}, {})
                bridge.state['thread'] = 't'
                job = {'text': 'pending', 'phase': InputPhase.PREPARING}
                bridge.state['inbox'].append(job)
                with patch('codex_wechat.service_adapter.notify', AsyncMock()) as notify:
                    await bridge.require_wechat_rebind()
                    await bridge.require_wechat_rebind()
                    self.assertFalse(bridge.dispatch_input(job, InputPhase.DISPATCHING, None, bridge.backend))
                    self.assertEqual(job['phase'], InputPhase.PREPARING)
                    reloaded = Bridge(store, bridge.account, {})
                    await reloaded.require_wechat_rebind()
                notify.assert_awaited_once()
                self.assertEqual(store.load('bridge')['inbox'][0]['text'], 'pending')
            finally:
                store.close()

    async def test_half_binding_is_not_treated_as_a_usable_login(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(temp)
            try:
                store.save('account', {'bot': 'b', 'user': 'u', 'token': 'fixture'})
                store.save('bridge', {'bindingPending': {'target': {'bot': 'b', 'user': 'u'}}})
                with self.assertRaisesRegex(ConfigurationError, '重新绑定'):
                    await startup_account(store)
            finally:
                store.close()

    def test_identity_change_preserves_agent_selection_and_user_preferences(self):
        state = {'thread': 'selected', 'backendName': 'deepseek',
                 'backendSessions': {'deepseek': {'thread': 'selected'}},
                 'processingNotice': 'kept', 'completionNotice': 'done',
                 'inbox': [{'text': 'old'}], 'outbox': [{'text': 'old reply'}],
                 'cursor': 'old cursor', 'context': 'old context', 'seen': ['old']}
        wechat_auth.prepare_identity_change(state, {'bot': 'new', 'user': 'new'})
        self.assertEqual(state['thread'], 'selected')
        self.assertEqual(state['processingNotice'], 'kept')
        self.assertEqual(state['backendSessions']['deepseek']['thread'], 'selected')
        self.assertEqual(state['inbox'], [])
        self.assertEqual(state['outbox'], [])
        self.assertNotIn('context', state)
