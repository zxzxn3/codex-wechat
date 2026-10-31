"""Small in-memory timing samples; no probes, prompt text or persistent history."""
from collections import deque
import math
import time
import uuid


class Latency:
    def __init__(self):
        self.run_id = uuid.uuid4().hex
        self.sends = deque(maxlen=20)
        self.agent = deque(maxlen=20)

    def probe(self, received=None):
        received = received or {}
        return {"run": self.run_id, "received": received.get("received", time.perf_counter()),
                "queued": time.perf_counter(), "ingress_ms": received.get("ingress_ms")}

    @staticmethod
    def received(source_ms=None):
        result = {"received": time.perf_counter()}
        if isinstance(source_ms, (int, float)) and math.isfinite(source_ms):
            estimate = time.time() * 1000 - source_ms
            if 0 <= estimate < 24 * 60 * 60 * 1000:
                result["ingress_ms"] = estimate
        return result

    def record_agent(self, first_ms, total_ms, *, backend=None, model=None):
        self.agent.append({"first": first_ms, "total": total_ms, "backend": backend, "model": model})

    @staticmethod
    def route(ingress, send_ms):
        incoming = f"{ingress:.0f}" if ingress is not None else "-"
        outgoing = f"{send_ms:.0f}" if send_ms is not None else "-"
        return f"{incoming}/{outgoing}ms"

    def notice(self, ingress_ms=None):
        return self.route(ingress_ms, self.sends[-1] if self.sends else None)

    def report(self, probe, send_ms):
        if probe["run"] != self.run_id:
            return "测速跨越了网关重启，计时已失效，请重新发送 /ping。"
        return self.route(probe.get("ingress_ms"), send_ms)
