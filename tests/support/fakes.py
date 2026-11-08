"""Small shared fakes whose call history is part of the test contract."""
from codex_wechat.service_adapter import ServiceState


class FakeServiceAdapter:
    def __init__(self, running=True):
        self.running = running
        self.calls = []

    async def status(self):
        self.calls.append("status")
        return ServiceState(True, self.running)

    async def stop_and_wait(self):
        self.calls.append("stop")
        self.running = False

    async def start(self):
        self.calls.append("start")
        self.running = True
