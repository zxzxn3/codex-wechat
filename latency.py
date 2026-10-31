"""Channel samples and on-demand connection timing; no model calls."""
import asyncio
from collections import deque
import json
import os
from urllib.parse import urlsplit, urlunsplit
import math
import time


class Latency:
    def __init__(self):
        self.sends = deque(maxlen=20)
        self.provider_sample = None

    @staticmethod
    def received(source_ms=None):
        result = {"received": time.perf_counter()}
        if isinstance(source_ms, (int, float)) and math.isfinite(source_ms):
            estimate = time.time() * 1000 - source_ms
            if 0 <= estimate < 24 * 60 * 60 * 1000:
                result["ingress_ms"] = estimate
        return result

    @staticmethod
    def _display(value):
        return f"{value:.0f}" if value is not None else "-"

    def values_in(self, ingress_ms, send_ms):
        return {"发送延迟": self._display(ingress_ms),
                "接收延迟": self._display(send_ms)}

    def values(self, ingress_ms=None):
        return self.values_in(ingress_ms, self.sends[-1] if self.sends else None)


async def connection_probe(endpoint, env):
    """HEAD without auth; report only completed TLS/TCP setup, not HTTP waiting."""
    url = urlsplit(endpoint)
    if url.scheme not in ("https", "http") or not url.hostname or url.username or url.password:
        return "unavailable"
    # Connection timing needs the origin only; discard configured path/query.
    target = urlunsplit((url.scheme, url.netloc, "/", "", ""))
    config = ("url = " + json.dumps(target) + "\n").encode()
    proc = await asyncio.create_subprocess_exec(
        "curl", "--disable", "--silent", "--head", "--globoff", "--proto", "=https,http",
        "--connect-timeout", "5", "--max-time", "6", "--output", os.devnull,
        "--write-out", "%{json}", "--config", "-", env=env,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL)
    try:
        output, _ = await asyncio.wait_for(proc.communicate(config), 8)
    finally:
        if proc.returncode is None:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), 1)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
    data = json.loads(output)
    elapsed = data.get("time_appconnect" if url.scheme == "https" else "time_connect", 0)
    # curl can time out waiting for HEAD even after successful setup. Keep the
    # measured setup time; never substitute time_total/time_starttransfer.
    if isinstance(elapsed, (int, float)) and math.isfinite(elapsed) and elapsed > 0:
        return f"{elapsed * 1000:.0f}ms"
    return "timeout" if proc.returncode == 28 else "unavailable"


async def provider_latency(backend):
    try:
        target = await asyncio.wait_for(backend.probe_target(), 3)
        if target is None:
            return "unavailable"
        return await connection_probe(*target)
    except asyncio.TimeoutError:
        return "timeout"
    except (OSError, ValueError, RuntimeError):
        return "unavailable"
