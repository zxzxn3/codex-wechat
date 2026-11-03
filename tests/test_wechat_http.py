"""Pool lifetime, concurrency and cancellation; never contact WeChat."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from curl_cffi import CurlInfo, CurlOpt
from curl_cffi.requests.exceptions import RequestException

from wechat_http import PooledHTTP, _metrics, _session

URL = 'https://ilinkai.weixin.qq.com/ilink/bot/getconfig'


def response(*, reused=False):
    return SimpleNamespace(status_code=200, infos={
        CurlInfo.NUM_CONNECTS: 0 if reused else 1,
        CurlInfo.USED_PROXY: 1,
        CurlInfo.CONNECT_TIME: 0 if reused else .02,
        CurlInfo.APPCONNECT_TIME: 0 if reused else .05,
        CurlInfo.PRETRANSFER_TIME: .001 if reused else .06,
        CurlInfo.STARTTRANSFER_TIME: .08, CurlInfo.TOTAL_TIME: .1})


class PoolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.sessions = []
        self.transport = PooledHTTP()
        self.factory = patch('wechat_http._session', side_effect=self.make_session)
        self.factory.start()

    async def asyncTearDown(self):
        await self.transport.close()
        self.factory.stop()

    def make_session(self, idle_seconds=60):
        async def request(method, url, **kwargs):
            kwargs['content_callback'](b'{"ret":0}')
            return response()
        session = SimpleNamespace(request=AsyncMock(side_effect=request), close=AsyncMock(),
                                  curl_options={CurlOpt.MAXAGE_CONN: idle_seconds})
        self.sessions.append(session)
        return session

    async def fetch(self, *, polling=False):
        return await self.transport.request(URL, {'Authorization': 'secret'}, b'{}',
                                            timeout=5, polling=polling)

    async def test_consecutive_requests_reuse_session_without_retry(self):
        first = await self.fetch()
        await self.fetch()
        self.assertEqual(len(self.sessions), 1)
        self.assertEqual(self.sessions[0].request.await_count, 2)
        self.assertEqual(first[0], b'{"ret":0}\n200')
        args = self.sessions[0].request.await_args
        self.assertEqual(args.kwargs['timeout'], (5, 0))
        self.assertEqual(args.kwargs['headers']['Authorization'], 'secret')

    async def test_poll_does_not_block_outgoing_request(self):
        entered, release = asyncio.Event(), asyncio.Event()
        poll_session = self.transport.poll.acquire()
        self.transport.poll.release()
        async def blocked(*args, **kwargs):
            entered.set()
            await release.wait()
            return response()
        poll_session.request.side_effect = blocked
        task = asyncio.create_task(self.fetch(polling=True))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            await asyncio.wait_for(self.fetch(), 1)
            self.assertEqual(len(self.sessions), 2)
            self.assertFalse(task.done())
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_idle_pool_expires_and_next_request_builds_new_session(self):
        self.transport.set_idle_seconds(.01)
        await self.fetch()
        idle = self.transport.outgoing.idle
        await asyncio.wait_for(asyncio.shield(idle), 1)
        self.sessions[0].close.assert_awaited_once()
        await self.fetch()
        self.assertEqual(len(self.sessions), 2)

    async def test_live_request_is_not_expired_and_cancel_releases_slot(self):
        pool = self.transport.outgoing
        session = pool.acquire()
        pool.release()
        entered = asyncio.Event()
        async def blocked(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()
        session.request.side_effect = blocked
        self.transport.set_idle_seconds(.01)
        task = asyncio.create_task(self.fetch())
        await asyncio.wait_for(entered.wait(), 1)
        try:
            await asyncio.sleep(.03)
            session.close.assert_not_awaited()
            self.assertEqual(pool.active, 1)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(pool.active, 0)
        self.assertIsNotNone(pool.idle)

    async def test_hot_change_reschedules_existing_idle_pool(self):
        await self.fetch()
        pool = self.transport.outgoing
        previous = pool.idle
        self.transport.set_idle_seconds(.01)
        self.assertIsNot(pool.idle, previous)
        self.assertEqual(self.sessions[0].curl_options[CurlOpt.MAXAGE_CONN], .01)
        await asyncio.wait_for(asyncio.shield(pool.idle), 1)
        self.sessions[0].close.assert_awaited_once()

    async def test_hot_change_preserves_active_request(self):
        session = self.transport.outgoing.acquire()
        self.transport.set_idle_seconds(2)
        self.assertIs(self.transport.outgoing.session, session)
        session.close.assert_not_awaited()
        self.assertIsNone(self.transport.outgoing.idle)
        self.assertEqual(session.curl_options[CurlOpt.MAXAGE_CONN], 2)
        self.transport.outgoing.release()
        await self.fetch(polling=True)
        self.assertEqual(self.sessions[1].curl_options[CurlOpt.MAXAGE_CONN], 2)

    async def test_zero_keeps_pool_but_explicit_close_still_releases_it(self):
        self.transport.set_idle_seconds(0)
        await self.fetch()
        pool = self.transport.outgoing
        self.assertIsNone(pool.idle)
        self.assertIsNotNone(pool.idle_since)
        await self.fetch()
        self.assertEqual(len(self.sessions), 1)
        self.assertIsNone(pool.idle)
        await self.transport.close()
        self.sessions[0].close.assert_awaited_once()

    async def test_hot_zero_cancels_timer_and_positive_reuses_original_idle_start(self):
        await self.fetch()
        pool = self.transport.outgoing
        old_timer = pool.idle
        self.transport.set_idle_seconds(0)
        self.assertIsNone(pool.idle)
        await asyncio.sleep(0)
        self.assertTrue(old_timer.cancelled())
        self.assertGreater(self.sessions[0].curl_options[CurlOpt.MAXAGE_CONN], 3600)
        pool.idle_since -= 10
        self.transport.set_idle_seconds(1)
        await asyncio.wait_for(asyncio.shield(pool.idle), 1)
        self.sessions[0].close.assert_awaited_once()

    async def test_transfer_failure_is_not_retried_or_exposed(self):
        session = self.transport.outgoing.acquire()
        self.transport.outgoing.release()
        session.request.side_effect = RequestException('SECRET-URL', 28, response())
        output, metrics = await self.fetch()
        self.assertEqual(metrics['curl_code'], 28)
        self.assertNotIn('SECRET', str(metrics))
        session.request.assert_awaited_once()

    async def test_chunked_response_limit_aborts_before_buffering_more(self):
        aborted = []
        async def too_large(*args, **kwargs):
            aborted.append(kwargs['content_callback'](b'12345'))
            raise RequestException('private', 23, response())
        session = self.transport.outgoing.acquire()
        self.transport.outgoing.release()
        session.request.side_effect = too_large
        with patch('wechat_http.MAX_RESPONSE_BYTES', 4):
            output, metrics = await self.fetch()
        self.assertEqual(aborted, [0xFFFFFFFF])
        self.assertEqual(metrics['curl_code'], 63)
        self.assertEqual(output, b'\n200')

    async def test_close_drains_timers_and_sessions(self):
        await self.fetch()
        await self.fetch(polling=True)
        await self.transport.close()
        for session in self.sessions:
            session.close.assert_awaited_once()
        self.assertFalse(self.transport.poll.tasks)
        self.assertFalse(self.transport.outgoing.tasks)
        with self.assertRaises(RuntimeError):
            await self.fetch()


class OptionsTests(unittest.TestCase):
    def test_session_has_tls_validation_no_redirects_and_native_idle_limit(self):
        with patch('curl_cffi.requests.AsyncSession') as factory:
            _session()
        options = factory.call_args.kwargs
        self.assertFalse(options['allow_redirects'])
        self.assertTrue(options['verify'])
        self.assertTrue(options['trust_env'])
        self.assertTrue(options['discard_cookies'])
        self.assertEqual(options['curl_options'][CurlOpt.MAXAGE_CONN], 60)
        self.assertEqual(options['curl_options'][CurlOpt.PROTOCOLS_STR], b'https')

    def test_zero_does_not_pass_native_zero_idle_age(self):
        with patch('curl_cffi.requests.AsyncSession') as factory:
            _session(0)
        self.assertGreater(factory.call_args.kwargs['curl_options'][CurlOpt.MAXAGE_CONN], 3600)

    def test_reused_connection_keeps_wait_timings_without_fake_tls(self):
        metrics = _metrics(response(reused=True))
        self.assertTrue(metrics['connection_reused'])
        self.assertTrue(metrics['proxy_used'])
        self.assertAlmostEqual(metrics['response_wait_ms'], 79)
        self.assertAlmostEqual(metrics['download_ms'], 20)
        self.assertNotIn('tls_ms', metrics)
