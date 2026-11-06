"""Backend connection and recovery coordination; no WeChat or delivery policy."""
import asyncio
import sys

from .commands import ensure_session
from .diagnostics import error_fields, log_event
from .errors import backend_reason, disposition


class BackendSession:
    """Own connection establishment and the background recovery loop."""

    def __init__(self, bridge):
        self.bridge = bridge

    async def ensure(self):
        bridge = self.bridge
        async with bridge.connection_lock:
            if bridge.backend_started and bridge.backend.connected:
                return
            bridge.backend_started = False
            bridge.loaded = False
            await bridge.backend.close()
            try:
                await bridge.backend.start()
            except BaseException:
                await bridge.backend.close()
                raise
            bridge.backend_started = True
            bridge.backend_connection(bridge.backend, True)
            bridge.input_paused = False
            bridge.wake.set()

    async def maintain(self):
        bridge = self.bridge
        reported = None
        while True:
            delay = 10
            if (not bridge.restarting and not bridge.running and not bridge.steering
                    and not bridge.session_lock.locked()):
                try:
                    async with bridge.session_lock:
                        bridge.backend_recovering = True
                        try:
                            await bridge.ensure_backend()
                            if bridge.state["thread"]:
                                await ensure_session(bridge)
                        finally:
                            bridge.backend_recovering = False
                    bridge.input_paused = False
                    bridge.wake.set()
                    reported = None
                except Exception as error:
                    bridge.backend_started = bridge.backend.connected
                    bridge.input_paused = True
                    reason = backend_reason(error)
                    if reason != reported:
                        bridge.reply(bridge.prompt("BACKEND_CONNECT_FAILED", {"reason": reason}))
                        reported = reason
                    retry_after = disposition(error).retry_after
                    if retry_after is not None:
                        delay = retry_after
                    log_event("backend.recover_failed", level="warning", component="backend",
                              retry_in_ms=int(delay * 1000), **error_fields(error))
                    print(f"后端恢复失败：{type(error).__name__}", file=sys.stderr, flush=True)
            if not bridge.restarting and bridge.backend_started and bridge.backend.connected:
                await bridge.backend.refresh_usage()
                await bridge.backend.rate_limits()
            await asyncio.sleep(delay)
