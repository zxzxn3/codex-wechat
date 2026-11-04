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

CURL_TIMING_FIELDS = {
    "time_namelookup": "dns_ms",
    "time_connect": "connect_ms",
    "time_appconnect": "appconnect_ms",
    "time_pretransfer": "pretransfer_ms",
    "time_starttransfer": "first_byte_ms",
    "time_total": "curl_total_ms",
}
_PHASE_PAIRS = {
    "tcp_ms": ("dns_ms", "connect_ms"),
    "tls_ms": ("connect_ms", "appconnect_ms"),
    "response_wait_ms": ("pretransfer_ms", "first_byte_ms"),
    "download_ms": ("first_byte_ms", "curl_total_ms"),
}
_POST_TLS_PHASES = {"response_wait_ms", "download_ms"}
NETWORK_TIMING_FIELDS = frozenset(CURL_TIMING_FIELDS.values()) | frozenset(_PHASE_PAIRS)
_PROXY_VARIABLES = ("http_proxy", "https_proxy", "all_proxy")
# Keep response body + HTTP code on stdout. Only explicitly selected numeric
# milestones go to stderr; never use curl's broad %{json} diagnostic object.
# Optional proxy telemetry stays on a separate line: curl <8.7 prints an
# unknown-variable warning, but still completes the ordinary numeric trailer.
CURL_WRITE_OUT = ("%{stderr}\ncodex_wechat_proxy_used=%{proxy_used}\n{" + ",".join(
    '"' + name + '":%{' + name + '}' for name in CURL_TIMING_FIELDS
) + "}%{stdout}\n%{http_code}")


def curl_timings(stderr, *, connection_reused=False):
    """Read only the numeric write-out trailer; discard curl error text entirely."""
    try:
        raw = json.loads(stderr.rsplit(b"\n", 1)[-1])
    except (ValueError, TypeError, AttributeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    values = {}
    for line in stderr.splitlines():
        if line in (b"codex_wechat_proxy_used=0", b"codex_wechat_proxy_used=1"):
            values["proxy_used"] = line.endswith(b"1")
    for name, field in CURL_TIMING_FIELDS.items():
        value = raw.get(name)
        if type(value) not in (int, float) or value < 0:
            continue
        try:
            value = float(value) * 1000
        except OverflowError:
            continue
        if math.isfinite(value):
            values[field] = value
    for field, (start, end) in _PHASE_PAIRS.items():
        before, after = values.get(start), values.get(end)
        if field in _POST_TLS_PHASES and not (connection_reused or (
                values.get("connect_ms", 0) > 0 and values.get("appconnect_ms", 0) > 0)):
            continue
        # Zero can mean that a milestone was never reached. Do not invent a
        # completed TLS/wait/download phase after a failed connection.
        if (before is not None and after is not None and after > 0
                and (before > 0 or start == "dns_ms") and after >= before):
            values[field] = after - before
    return values


def timeout_stage(timings):
    """Classify only reached HTTPS milestones, never infer a remote root cause."""
    if timings.get("connection_reused"):
        return "response_body" if timings.get("first_byte_ms", 0) > 0 else "request_or_response"
    if "connect_ms" not in timings or "appconnect_ms" not in timings:
        return "unknown"
    if timings["connect_ms"] <= 0:
        return "connect"
    if timings["appconnect_ms"] <= 0:
        return "tls_or_tunnel"
    if "first_byte_ms" not in timings:
        return "unknown"
    return "response_body" if timings["first_byte_ms"] > 0 else "request_or_response"


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
        return {"ingress_ms": self._display(ingress_ms),
                "egress_ms": self._display(send_ms)}

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
    if proc.returncode:
        # Say which path failed: a direct attempt and a proxied one fail for
        # entirely different reasons, and a bare label hides that.
        kind = "timeout" if proc.returncode == 28 else "unreachable"
        return kind + "(" + probe_reason(env) + ")"
    return "unavailable(空响应)"


def probe_reason(env):
    """Name the network path a probe used, for failure reports only."""
    configured = {key.lower() for key, value in env.items() if str(value).strip()}
    return ("代理未响应" if any(name in configured for name in _PROXY_VARIABLES)
            else "直连,未配置代理")


async def provider_latency(backend):
    try:
        target = await asyncio.wait_for(backend.probe_target(), 3)
        if target is None:
            return "unavailable(无可用目标)"
        return await connection_probe(*target)
    except TimeoutError:
        return "timeout(读取后端配置)"
    except (OSError, ValueError, RuntimeError) as error:
        # The class is the only clue a user can report back; texts are unsafe.
        return f"unavailable({type(error).__name__})"
