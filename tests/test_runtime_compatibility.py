"""Source compatibility checks are pure and never read the real user config."""
import json
from pathlib import Path
import unittest
from installer import check_runtime_compatibility

class RuntimeCompatibilityTests(unittest.TestCase):
    def test_legacy_source_cannot_replace_active_websocket_mode(self):
        config={"backends":{"codex":{"websocketUrl":"ws://127.0.0.1:45231"}}}
        with self.assertRaisesRegex(ValueError, "WebSocket"):
            check_runtime_compatibility({}, config)
    def test_matching_declaration_accepts_shared_mode(self):
        content=b'SUPPORTED_TRANSPORTS = ("stdio", "native-daemon", "loopback-websocket")\n'
        files={Path("codex_connection.py"):(content,0o600)}
        check_runtime_compatibility(files,{"backend":{"websocketUrl":"ws://127.0.0.1:45231"}})
    def test_regular_legacy_config_does_not_require_new_manifest(self):
        check_runtime_compatibility({}, {"backends":{"codex":{}}})
    def test_unrelated_declaration_does_not_prove_websocket_support(self):
        files={Path("codex_connection.py"):(b'SUPPORTED_TRANSPORTS = ("stdio",)\n',0o600)}
        with self.assertRaises(ValueError):
            check_runtime_compatibility(files,{"backend":{"websocketUrl":"ws://127.0.0.1:45231"}})
