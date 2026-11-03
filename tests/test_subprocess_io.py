"""Cancellation is observable only after child exit and pipe closure."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from subprocess_io import communicate

class Process:
    def __init__(self, *, ignores_terminate=False):
        self.returncode=None
        self.entered=asyncio.Event()
        self.exited=asyncio.Event()
        self.ignores_terminate=ignores_terminate
        self.closed=False
        self.killed=False
        self.stdin=SimpleNamespace(close=Mock(), wait_closed=self.wait_closed)
    async def wait_closed(self):
        await asyncio.sleep(0)
        self.closed=True
    async def communicate(self, data):
        self.entered.set()
        await self.exited.wait()
        return b"partial output", None
    def terminate(self):
        if not self.ignores_terminate:
            self.returncode=-15
            self.exited.set()
    def kill(self):
        self.killed=True
        self.returncode=-9
        self.exited.set()
    async def wait(self):
        await self.exited.wait()
        return self.returncode

class SubprocessIOTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_does_not_escape_before_reaping_and_closing(self):
        process=Process()
        task=asyncio.create_task(communicate(process,b"fixture",timeout=10))
        await process.entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIsNotNone(process.returncode)
        self.assertTrue(process.closed)
    async def test_timeout_kills_unresponsive_child_and_closes_pipes(self):
        process=Process(ignores_terminate=True)
        with self.assertRaises(asyncio.TimeoutError):
            await communicate(process, timeout=0.01, grace=0)
        self.assertTrue(process.killed)
        self.assertTrue(process.closed)
    async def test_normal_output_and_nonzero_returncode_are_not_rewritten(self):
        process=Process()
        process.returncode=63
        process.exited.set()
        output=await communicate(process,timeout=1)
        self.assertEqual(output,(b"partial output",None))
        self.assertEqual(process.returncode,63)
        self.assertTrue(process.closed)
