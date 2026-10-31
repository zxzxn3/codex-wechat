"""Small in-memory timing samples; no probes, prompt text or persistent history."""
from collections import deque
import math
import statistics
import time
import uuid


def duration(milliseconds):
    return f"{milliseconds:.0f} ms" if milliseconds < 1000 else f"{milliseconds / 1000:.2f} s"


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

    def report(self, probe, send_started, send_ms):
        if probe["run"] != self.run_id:
            return "测速跨越了网关重启，计时已失效，请重新发送 /ping。"
        total_ms = (send_started - probe["received"]) * 1000 + send_ms
        queue_ms = max(0, (send_started - probe["queued"]) * 1000)
        lines = [f"收信→微信接口确认：{duration(total_ms)}",
                 f"其中等待/重试 {duration(queue_ms)}；发送 API {duration(send_ms)}"]
        if probe.get("ingress_ms") is not None:
            lines.append(f"消息时间戳→网关 ≈ {duration(probe['ingress_ms'])}（跨时钟估算）")
        if self.sends:
            lines.append(f"最近发送 API 中位数：{duration(statistics.median(self.sends))}（{len(self.sends)}次成功请求）")
        if self.agent:
            sample = self.agent[-1]
            first = duration(sample["first"]) if sample["first"] is not None else "无文字样本"
            label = "/".join(str(sample[k]) for k in ("backend", "model") if sample.get(k))
            lines.append(f"最近 Agent{' [' + label + ']' if label else ''}：首条文字 {first}；全程 {duration(sample['total'])}")
        else:
            lines.append("Agent 耗时：暂无本进程的完成样本")
        lines.append("本次未调用模型；不含手机下行显示，非完整手机 RTT。")
        return "\n".join(lines)
