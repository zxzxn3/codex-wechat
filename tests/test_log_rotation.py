"""Console-log rotation is bounded and follows logs.keep."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import log_rotation


class LogRotationTests(unittest.TestCase):
    def test_append_rotates_at_the_size_boundary_and_prunes_old_generations(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "logs" / "service.log"
            for data in (b"a" * 100, b"b" * 100, b"c" * 100, b"d" * 100):
                log_rotation.append(path, data, keep=2, max_bytes=100)

            self.assertEqual(path.read_bytes(), b"d" * 100)
            self.assertEqual(path.with_name("service.log.1").read_bytes(), b"c" * 100)
            self.assertEqual(path.with_name("service.log.2").read_bytes(), b"b" * 100)
            self.assertFalse(path.with_name("service.log.3").exists())

    def test_keep_provider_is_read_when_a_rotation_is_needed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "logs" / "service.log"
            config = root / "config.json"
            config.write_text(json.dumps({"logs": {"keep": 1}}), encoding="utf-8")

            def keep():
                return log_rotation.keep_from_config(config)

            for data in (b"a" * 100, b"b" * 100, b"c" * 100):
                log_rotation.append(path, data, keep, max_bytes=100)

            self.assertTrue(path.with_name("service.log.1").exists())
            self.assertFalse(path.with_name("service.log.2").exists())

    def test_lowering_keep_removes_stale_generations(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "service.log"
            for number in range(1, 7):
                path.with_name(f"service.log.{number}").write_bytes(b"x")
            log_rotation.prune_rotations(path, 2)
            self.assertEqual(
                sorted(item.name for item in path.parent.glob("service.log.*")),
                ["service.log.1", "service.log.2"])

    def test_failed_rotation_keeps_writing_instead_of_blocking(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "service.log"
            log_rotation.append(path, b"a" * 100, keep=2, max_bytes=100)
            with patch.object(log_rotation.os, "replace", side_effect=OSError("busy")):
                log_rotation.append(path, b"b", keep=2, max_bytes=100)
            self.assertEqual(path.read_bytes(), b"a" * 100 + b"b")

    def test_invalid_config_falls_back_to_the_default_keep(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.json"
            config.write_text('{"logs":{"keep":0}}', encoding="utf-8")
            self.assertEqual(log_rotation.keep_from_config(config),
                             log_rotation.DEFAULT_KEEP)


if __name__ == "__main__":
    unittest.main()
