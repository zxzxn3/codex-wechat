"""Only numeric curl milestones cross the request/diagnostic boundary."""
import asyncio
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import diagnostics
from latency import CURL_WRITE_OUT, curl_timings, timeout_stage
from wechat import WeChat


class NetworkTimingTests(unittest.TestCase):
    def test_milestones_and_deltas_have_distinct_units_and_meanings(self):
        raw = {'time_namelookup': .01, 'time_connect': .04, 'time_appconnect': .1,
               'time_pretransfer': .11, 'time_starttransfer': .3, 'time_total': .35}
        result = curl_timings(json.dumps(raw).encode())
        expected = {'dns_ms': 10, 'connect_ms': 40, 'appconnect_ms': 100,
                    'pretransfer_ms': 110, 'first_byte_ms': 300, 'curl_total_ms': 350,
                    'tcp_ms': 30, 'tls_ms': 60, 'response_wait_ms': 190, 'download_ms': 50}
        self.assertEqual(set(result), set(expected))
        for name, value in expected.items():
            self.assertAlmostEqual(result[name], value)

    def test_partial_timeout_does_not_invent_completed_tls_or_response(self):
        raw = {'time_namelookup': .005, 'time_connect': .02, 'time_appconnect': 0,
               'time_pretransfer': 0, 'time_starttransfer': 0, 'time_total': 10}
        result = curl_timings(b'curl: (28) SECRET-ERROR\n' + json.dumps(raw).encode())
        self.assertEqual(result['curl_total_ms'], 10000)
        self.assertAlmostEqual(result['tcp_ms'], 15)
        for field in ('tls_ms', 'response_wait_ms', 'download_ms'):
            self.assertNotIn(field, result)
        self.assertNotIn('SECRET', json.dumps(result))

    def test_missing_invalid_and_unknown_fields_are_not_measured(self):
        for value in (None, b'', b'error text', b'[]', b'null', b'\xff'):
            self.assertEqual(curl_timings(value), {})
        for value in (True, '0.2', -1, float('nan'), float('inf'), 10**400):
            with self.subTest(value=repr(value)[:40]):
                self.assertEqual(curl_timings(json.dumps({'time_total': value,
                    'secret': 'SECRET-CREDENTIAL'}).encode()), {})

    def test_nonmonotonic_and_unreached_milestones_have_no_negative_phases(self):
        result = curl_timings(json.dumps({'time_namelookup': .1, 'time_connect': .05,
            'time_appconnect': .02, 'time_pretransfer': .15,
            'time_starttransfer': .1, 'time_total': .09}).encode())
        for field in ('tcp_ms', 'tls_ms', 'response_wait_ms', 'download_ms'):
            self.assertNotIn(field, result)
        result = curl_timings(b'{"time_namelookup":0,"time_connect":0.01}')
        self.assertEqual(result['tcp_ms'], 10)

    def test_write_out_selects_numeric_fields_not_broad_diagnostics(self):
        self.assertIn('%{stderr}', CURL_WRITE_OUT)
        self.assertTrue(CURL_WRITE_OUT.endswith('%{stdout}\n%{http_code}'))
        for forbidden in ('%{json}', '%{url', '%{errormsg}', '%{remote_ip}'):
            self.assertNotIn(forbidden, CURL_WRITE_OUT)

    def test_optional_proxy_marker_does_not_break_older_curl_timings(self):
        trailer = b'{"time_connect":0.02,"time_appconnect":0.05,"time_total":1}'
        for value, expected in ((b'0', False), (b'1', True)):
            result = curl_timings(b'codex_wechat_proxy_used=' + value + b'\n' + trailer)
            self.assertIs(result['proxy_used'], expected)
            self.assertEqual(result['connect_ms'], 20)
        older = b"codex_wechat_proxy_used=curl: unknown --write-out variable: 'proxy_used'\n\n"
        result = curl_timings(older + trailer)
        self.assertNotIn('proxy_used', result)
        self.assertEqual(result['appconnect_ms'], 50)
        self.assertNotIn('proxy_used', curl_timings(b'codex_wechat_proxy_used=2\n' + trailer))

    def test_timeout_stage_never_claims_to_identify_a_remote_cause(self):
        for values, expected in (
            ({}, 'unknown'),
            ({'connect_ms': 0, 'appconnect_ms': 0, 'first_byte_ms': .1}, 'connect'),
            ({'connect_ms': 10, 'appconnect_ms': 0}, 'tls_or_tunnel'),
            ({'connect_ms': 10, 'appconnect_ms': 50}, 'unknown'),
            ({'connect_ms': 10, 'appconnect_ms': 50, 'first_byte_ms': 0}, 'request_or_response'),
            ({'connect_ms': 10, 'appconnect_ms': 50, 'first_byte_ms': 100}, 'response_body'),
        ):
            with self.subTest(values=values):
                self.assertEqual(timeout_stage(values), expected)


class RequestTimingTests(unittest.IsolatedAsyncioTestCase):
    async def test_curl_failure_still_logs_timings_once_without_raw_error(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'bridge.jsonl'
            diagnostics.configure(path=path, level='debug')
            process = SimpleNamespace(returncode=28)
            stderr = b'curl: (28) SECRET-ERROR\n{"time_total":10,"time_connect":0.1}'
            try:
                with patch('wechat.asyncio.create_subprocess_exec', AsyncMock(return_value=process)) as spawn, \
                     patch('wechat.communicate', AsyncMock(return_value=(b'\n000', stderr))):
                    with self.assertRaisesRegex(RuntimeError, 'curl 28'):
                        await WeChat({'token': 'SECRET-TOKEN'}).poll('SECRET-CURSOR')
                self.assertEqual(spawn.await_args.kwargs['stderr'], asyncio.subprocess.PIPE)
                records = [json.loads(line) for line in path.read_text().splitlines()]
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0]['curl_total_ms'], 10000)
                self.assertEqual(records[0]['connect_ms'], 100)
                self.assertFalse(records[0]['ok'])
                self.assertNotIn('SECRET', path.read_text())
            finally:
                diagnostics.reset()

    async def test_success_returns_original_body_and_total_wall_clock_stays_separate(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'bridge.jsonl'
            diagnostics.configure(path=path, level='debug')
            process = SimpleNamespace(returncode=0)
            try:
                with patch('wechat.asyncio.create_subprocess_exec', AsyncMock(return_value=process)), \
                     patch('wechat.communicate', AsyncMock(return_value=(b'{"ret":0}\n200',
                            b'{"time_connect":0.02,"time_appconnect":0.05,'
                            b'"time_pretransfer":0.06,"time_starttransfer":0.08,"time_total":0.1}'))), \
                     patch('wechat.time.perf_counter', side_effect=[0, .5]):
                    result = await WeChat({'token': 'fixture'}).request('ilink/bot/getconfig')
                self.assertEqual(result, {'ret': 0})
                record = json.loads(path.read_text())
                self.assertEqual(record['duration_ms'], 500)
                self.assertEqual(record['curl_total_ms'], 100)
                self.assertEqual(record['download_ms'], 20)
            finally:
                diagnostics.reset()
