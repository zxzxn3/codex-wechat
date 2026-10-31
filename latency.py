"""Small in-memory timing samples; no probes, prompt text or persistent history."""
from collections import deque
import math
import time


class Latency:
    def __init__(self):
        self.sends = deque(maxlen=20)

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
