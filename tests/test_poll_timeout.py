"""Long-poll deadlines do not create receive gaps or hide connection failures."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

import diagnostics
from codex_wechat import Bridge
from errors import WeChatPollTimeout
from storage import Store
from wechat import WeChat


class PollTimeoutTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.log = Path(self.temp.name) / 'bridge.jsonl'
        diagnostics.configure(path=self.log, level='debug')
        self.client = WeChat({'token': 'PRIVATE-TOKEN'})

    async def asyncTearDown(self):
        await self.client.close()
        diagnostics.reset()
        self.temp.cleanup()

    def records(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def timings(self, **changes):
        return dict(curl_code=28, connect_ms=1, appconnect_ms=10,
                    pretransfer_ms=11, first_byte_ms=0, curl_total_ms=40000, **changes)

    async def test_wait_deadline_is_debug_control_flow_not_console_failure(self):
        metrics = self.timings()
        with patch.object(self.client.http, 'request', AsyncMock(return_value=(b'\n0', metrics))) as request, \
                patch('wechat.print') as console:
            with self.assertRaises(WeChatPollTimeout):
                await self.client.poll('PRIVATE-CURSOR')
        self.assertEqual(request.await_args.kwargs['timeout'], 40)
        console.assert_not_called()
        record = self.records()[-1]
        self.assertEqual(record['status'], 'poll_idle_timeout')
        self.assertEqual(record['level'], 'debug')
        self.assertNotIn('PRIVATE', json.dumps(record))

    async def test_connection_tls_body_and_send_timeouts_still_fail(self):
        for endpoint, changes, output in (
            ('ilink/bot/getupdates', {'connect_ms': 0, 'appconnect_ms': 0}, b'\n0'),
            ('ilink/bot/getupdates', {'appconnect_ms': 0}, b'\n0'),
            ('ilink/bot/getupdates', {'first_byte_ms': 20}, b'partial\n200'),
            ('ilink/bot/getupdates', {'pretransfer_ms': 0}, b'\n0'),
            ('ilink/bot/getupdates', {}, b'\n503'),
            ('ilink/bot/sendmessage', {}, b'\n0'),
        ):
            metrics = self.timings()
            metrics.update(changes)
            with self.subTest(endpoint=endpoint, changes=changes), \
                    patch.object(self.client.http, 'request', AsyncMock(return_value=(output, metrics))):
                with self.assertRaises(RuntimeError) as caught:
                    await self.client.request(endpoint)
                self.assertNotIsInstance(caught.exception, WeChatPollTimeout)
                self.assertEqual(self.records()[-1]['level'], 'warning')

    async def test_reused_connection_wait_is_also_idle(self):
        metrics = self.timings()
        metrics.update(connect_ms=0, appconnect_ms=0, connection_reused=True)
        with patch.object(self.client.http, 'request', AsyncMock(return_value=(b'\n0', metrics))):
            with self.assertRaises(WeChatPollTimeout):
                await self.client.poll('cursor')

    async def test_server_wait_hint_is_bounded_and_does_not_change_send_timeout(self):
        with patch.object(self.client, 'request', AsyncMock(return_value={
                'msgs': [], 'longpolling_timeout_ms': 60000})) as request:
            await self.client.poll('cursor')
            self.assertEqual(request.await_args.kwargs['timeout'], 40)
            await self.client.poll('cursor')
            self.assertEqual(request.await_args.kwargs['timeout'], 65)
            for invalid in (True, '1000', -1, 0, None):
                request.return_value = {'longpolling_timeout_ms': invalid}
                await self.client.poll('cursor')
                self.assertEqual(self.client.poll_timeout, 60)
            request.return_value = {'longpolling_timeout_ms': 10**9}
            await self.client.poll('cursor')
            self.assertEqual(self.client.poll_timeout, 120)
            await self.client.send({'id': 'id', 'user': 'owner', 'context': 'ctx', 'text': 'x'})
            self.assertEqual(request.await_args.kwargs['timeout'], 5)

    async def test_poller_immediately_repolls_idle_but_backs_off_connection_failure(self):
        store = Store(Path(self.temp.name) / 'state')
        bridge = Bridge(store, {'user': 'owner'}, {}, channel=self.client)
        bridge.state['cursor'] = 'unchanged'
        bridge.mark_wechat_valid = Mock()
        try:
            with patch.object(self.client, 'poll', AsyncMock(side_effect=[
                    WeChatPollTimeout(), RuntimeError('unavailable'), asyncio.CancelledError()])) as poll, \
                    patch('codex_wechat.asyncio.sleep', AsyncMock()) as sleep:
                with self.assertRaises(asyncio.CancelledError):
                    await bridge.poller()
            self.assertEqual(poll.await_count, 3)
            sleep.assert_awaited_once_with(2)
            bridge.mark_wechat_valid.assert_not_called()
            self.assertEqual(bridge.state['cursor'], 'unchanged')
            self.assertEqual(sum(r['event'] == 'wechat.poll_failed' for r in self.records()), 1)
        finally:
            store.close()

    async def test_business_errors_log_only_fixed_hints(self):
        for message, kind in (
            ('prepare failed', 'prepare_failed'),
            ('Unknown Error', 'unknown_error'),
            ('Too Many Requests', 'rate_limit_hint'),
            ('PRIVATE-TOKEN PRIVATE-BODY https://private/path', 'other'),
        ):
            output = json.dumps({'ret': -2, 'errmsg': message}).encode() + b'\n200'
            with patch.object(self.client.http, 'request', AsyncMock(return_value=(output, {'curl_code': 0}))):
                with self.assertRaises(RuntimeError):
                    await self.client.request('ilink/bot/sendmessage')
            record = self.records()[-1]
            self.assertEqual(record['api_error_kind'], kind)
            self.assertEqual(record['error_code'], '-2')
        diagnostics.log_event('test.labels', api_error_kind='PRIVATE-BODY')
        self.assertNotIn('api_error_kind', self.records()[-1])
        self.assertNotIn('PRIVATE', self.log.read_text())
