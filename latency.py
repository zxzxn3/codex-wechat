"""Channel samples and on-demand connection timing; no model calls."""
import asyncio
from subprocess_io import communicate
from collections import deque
import json
import os
from urllib.parse import urlsplit, urlunsplit
import math
import time
from platform_support import curl_command, subprocess_options


def _timestamp(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return value
    return None


def message_timestamps(msg):
    """Return usable message timestamps in preference order.

    The protocol documents create_time_ms as optional; some messages only
    carry update_time_ms or item timestamps. These fallbacks are estimates,
    not proof of the original send time. Keep the fallback local and do not
    invent a timestamp when every field is absent.
    """
    if not isinstance(msg, dict):
        return ()
    values = [msg.get("create_time_ms"), msg.get("update_time_ms")]
    for item in msg.get("item_list") or ():
        if isinstance(item, dict):
            values.extend((item.get("create_time_ms"), item.get("update_time_ms")))
    return tuple(value for value in (_timestamp(source) for source in values)
                 if value is not None)


class Latency:
    def __init__(self):
        self.sends = deque(maxlen=20)
        self.provider_sample = None
        self.started_ms = time.time() * 1000

    def received(self, source_ms=None):
        return self.received_any((source_ms,))

    def received_any(self, sources):
        result = {"received": time.perf_counter()}
        now = time.time() * 1000
        for source in sources:
            source_ms = _timestamp(source)
            if source_ms is None or source_ms < self.started_ms:
                continue
            estimate = now - source_ms
            if 0 <= estimate < 24 * 60 * 60 * 1000:
                result["ingress_ms"] = estimate
                break
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
        curl_command(), "--disable", "--silent", "--head", "--globoff", "--proto", "=https,http",
        "--connect-timeout", "5", "--max-time", "6", "--output", os.devnull,
        "--write-out", "%{json}", "--config", "-", env=env,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL, **subprocess_options())
    output, _ = await communicate(proc, config, timeout=8, grace=1)
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
