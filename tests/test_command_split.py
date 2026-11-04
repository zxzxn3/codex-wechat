"""Command handlers keep one registry and one session-mutation barrier."""
import inspect
import tempfile
import unittest
from pathlib import Path

import commands
from codex_wechat import Bridge
from command_specs import COMMANDS, HELP_ALL, REGISTRY
from commands import HANDLERS, handle
from errors import UserFacingError
from storage import Store


class CommandSplitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "state")
        self.bridge = Bridge(self.store, {"user": "owner"}, {})
        self.bridge.state.update(context="context", thread="thread")

    async def asyncTearDown(self):
        self.store.lock.close()
        self.temp.cleanup()

    async def test_every_registered_handler_is_callable_and_documented(self):
        self.assertFalse(hasattr(commands, "command_session"))
        for spec in COMMANDS:
            if spec.route == "input":
                continue
            handler = HANDLERS[spec.name]
            self.assertTrue(callable(handler), spec.name)
            self.assertTrue(inspect.iscoroutinefunction(handler), spec.name)
            self.assertIn(spec.help_usage or spec.usage, HELP_ALL, spec.name)
            self.assertTrue(spec.description, spec.name)
            for alias in spec.aliases:
                self.assertIs(REGISTRY[alias], spec, alias)

    async def test_session_mutations_share_the_existing_busy_gate(self):
        self.bridge.running = True
        original = dict(self.bridge.state)
        for command, argument in (
                ("/switch", "target"), ("/new", ""), ("/compact", ""),
                ("/effort", "high"), ("/model", "2"), ("/cwd", "/tmp")):
            with self.subTest(command=command):
                with self.assertRaises(UserFacingError) as raised:
                    await handle(self.bridge, command, argument)
                self.assertEqual(str(raised.exception),
                                 self.bridge.prompt("BUSY_CHANGE"))
                self.assertEqual(self.bridge.state["thread"], original["thread"])
                self.assertEqual(self.bridge.state["inbox"], original["inbox"])
                self.assertEqual(self.bridge.state["outbox"], original["outbox"])
        self.bridge.running = False
        self.bridge.state["inbox"] = [{"id": "queued"}]
        with self.assertRaises(UserFacingError) as raised:
            await handle(self.bridge, "/switch", "target")
        self.assertEqual(str(raised.exception), self.bridge.prompt("BUSY_CHANGE"))

    async def test_usage_checks_stay_before_the_busy_gate(self):
        self.bridge.running = True
        for command, argument in (("/compact", "extra"), ("/new", "extra"),
                                  ("/switch", "")):
            with self.subTest(command=command):
                with self.assertRaises(UserFacingError) as raised:
                    await handle(self.bridge, command, argument)
                self.assertNotEqual(str(raised.exception),
                                    self.bridge.prompt("BUSY_CHANGE"))


if __name__ == "__main__":
    unittest.main()
