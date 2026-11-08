"""Trace command waiting and output waiting without logging input content."""
import asyncio
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from codex_wechat.app import Bridge
from codex_wechat.commands import dispatch
from codex_wechat import diagnostics
from codex_wechat.storage import Store
from codex_wechat.wechat import WeChat
from tests.support.http_fixtures import exchange


class DiagnosticStageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.log = root / 'bridge.jsonl'
        diagnostics.configure(path=self.log, level='debug')
        self.store = Store(root / 'state')
        self.bridge = Bridge(self.store, {'user': 'owner'}, {})
        self.bridge.state['context'] = 'context'
        self.bridge.ensure_backend = AsyncMock()

    def tearDown(self):
        self.store.close()
        diagnostics.reset()
        self.temp.cleanup()

    def records(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    async def test_alias_reports_canonical_command_wait_and_execution(self):
        await dispatch(self.bridge, '/s', received={
            'request_id': 'req-command', 'received': time.perf_counter() - .25})
        records = self.records()
        start = next(record for record in records if record['event'] == 'control.started')
        end = next(record for record in records if record['event'] == 'control.completed')
        self.assertEqual(start['command'], '/status')
        self.assertEqual(end['command'], '/status')
        self.assertEqual(end['request_id'], 'req-command')
        self.assertGreaterEqual(start['queue_wait_ms'], 250)
        self.assertGreaterEqual(end['duration_ms'], 0)
        self.bridge.ensure_backend.assert_not_called()

    async def test_invalid_arguments_are_not_logged_or_sent_to_backend(self):
        await dispatch(self.bridge, '/s PRIVATE-SENTINEL')
        self.assertNotIn('PRIVATE', self.log.read_text())
        rejected = [record for record in self.records() if record['event'] == 'control.rejected']
        self.assertTrue(any(record.get('command') == '/status' for record in rejected))
        self.assertTrue(all('duration_ms' in record for record in rejected))
        self.bridge.ensure_backend.assert_not_called()

    def test_command_and_timeout_stage_schema_reject_arbitrary_text(self):
        for command in ('/switch SECRET', '/private-secret', 'PRIVATE-SENTINEL'):
            diagnostics.log_event('test.fields', command=command, timeout_stage='PRIVATE-SENTINEL')
        self.assertTrue(all('command' not in record and 'timeout_stage' not in record
                            for record in self.records()))
        diagnostics.log_event('test.alias', command='/s', timeout_stage='request_or_response')
        self.assertEqual(self.records()[-1]['command'], '/status')

    async def send_one(self):
        self.bridge.channel.send = AsyncMock(return_value={})
        task = asyncio.create_task(self.bridge.sender())
        try:
            await asyncio.wait_for(self.bridge.outbox_drained.wait(), 1)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_output_wait_is_measured_without_persisting_monotonic_time(self):
        self.bridge.reply('reply')
        entry = self.bridge.state['outbox'][0]
        self.bridge.output._queued_at[entry['id']] -= .25
        with patch('codex_wechat.output_delivery.print') as console:
            await self.send_one()
        console.assert_not_called()
        start = next(record for record in self.records() if record['event'] == 'output.send_start')
        self.assertGreaterEqual(start['queue_wait_ms'], 250)
        self.assertEqual(self.bridge.output._queued_at, {})
        self.assertNotIn('queue_wait_ms', entry)

    async def test_recovered_output_has_no_invented_wait_sample(self):
        self.bridge.reply('old reply')
        self.bridge.output._queued_at.clear()
        await self.send_one()
        start = next(record for record in self.records() if record['event'] == 'output.send_start')
        self.assertNotIn('queue_wait_ms', start)

    async def test_request_timeout_logs_phase_and_proxy_without_raw_stderr(self):
        trailer = (b'SECRET-ERROR\ncodex_wechat_proxy_used=1\n'
                   b'{"time_connect":0.01,"time_appconnect":0.05,"time_starttransfer":0,"time_total":5}')
        with exchange(b'\n000', code=28, stderr=trailer):
            with self.assertRaises(RuntimeError):
                await WeChat({'token': 'PRIVATE-TOKEN'}).request('ilink/bot/sendmessage')
        record = self.records()[-1]
        self.assertEqual(record['timeout_stage'], 'request_or_response')
        self.assertIs(record['proxy_used'], True)
        self.assertNotIn('SECRET', self.log.read_text())
        self.assertNotIn('PRIVATE', self.log.read_text())
