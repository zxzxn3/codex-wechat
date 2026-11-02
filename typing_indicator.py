"""Best-effort WeChat typing indicator tied to bridge work."""
import asyncio

from diagnostics import error_fields, log_event
from errors import WeChatAuthExpired

KEEPALIVE_SECONDS = 5


class TypingIndicator:
    def __init__(self, bridge):
        self.bridge = bridge
        self.active = 0
        self.ticket = ""
        self.ticket_key = None
        self.user = None
        self.context = None
        self.task = None
        self.lock = asyncio.Lock()

    async def _get_ticket(self):
        account = self.bridge.account
        key = (account.get("bot"), account.get("user"))
        getter = getattr(self.bridge.channel, "typing_ticket", None)
        if not account.get("token") or getter is None:
            return ""
        if self.ticket and self.ticket_key == key:
            return self.ticket
        ticket = await getter(account["user"], self.context)
        self.ticket = ticket if isinstance(ticket, str) else ""
        self.ticket_key = key
        return self.ticket

    async def _set_status(self, status):
        sender = getattr(self.bridge.channel, "set_typing", None)
        if sender is None or not self.ticket or not self.user:
            return
        await sender(self.user, self.ticket, status)

    async def _keepalive(self):
        while True:
            await asyncio.sleep(KEEPALIVE_SECONDS)
            try:
                await self._get_ticket()
                await self._set_status(1)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                await self._failed(error, "keepalive")
                if isinstance(error, WeChatAuthExpired):
                    return

    async def _failed(self, error, phase):
        self.ticket = ""
        self.ticket_key = None
        log_event("wechat.typing_failed", level="warning", component="wechat",
                  phase=phase, **error_fields(error))
        if isinstance(error, WeChatAuthExpired):
            await self.bridge.require_wechat_rebind()

    async def start(self, user, context):
        async with self.lock:
            self.active += 1
            if self.active > 1:
                return True
            self.user, self.context = user, context
            try:
                if not await self._get_ticket():
                    self.active = 0
                    return False
                await self._set_status(1)
            except asyncio.CancelledError:
                self.active = 0
                raise
            except Exception as error:
                self.active = 0
                await self._failed(error, "start")
                return False
            self.task = asyncio.create_task(self._keepalive())
            log_event("wechat.typing_started", component="wechat")
            return True

    async def stop(self):
        async with self.lock:
            if self.active <= 0:
                return
            self.active -= 1
            if self.active:
                return
            task, self.task = self.task, None
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            try:
                await self._set_status(2)
            except Exception as error:
                await self._failed(error, "stop")
                return
            log_event("wechat.typing_stopped", component="wechat")
