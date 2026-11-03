"""Best-effort WeChat typing indicator tied to bridge work."""
import asyncio
import time

from diagnostics import error_fields, log_event
from errors import WeChatAuthExpired

KEEPALIVE_SECONDS = 5
TICKET_CACHE_SECONDS = 600


class TypingIndicator:
    def __init__(self, bridge):
        self.bridge = bridge
        self.active = 0
        self.ticket = ""
        self.ticket_key = None
        self.ticket_expires = 0
        self.user = None
        self.context = None
        self.task = None
        self.started = False
        self.lock = asyncio.Lock()

    async def _get_ticket(self):
        account = self.bridge.account
        key = (account.get("bot"), account.get("user"))
        getter = getattr(self.bridge.channel, "typing_ticket", None)
        if not account.get("token") or getter is None:
            return ""
        if (self.ticket and self.ticket_key == key
                and time.monotonic() < self.ticket_expires):
            return self.ticket
        self.ticket = ""
        self.ticket_expires = 0
        ticket = await getter(account["user"], self.context)
        self.ticket = ticket if isinstance(ticket, str) else ""
        self.ticket_key = key
        if self.ticket:
            self.ticket_expires = time.monotonic() + TICKET_CACHE_SECONDS
        return self.ticket

    async def _set_status(self, status):
        sender = getattr(self.bridge.channel, "set_typing", None)
        if sender is None or not self.user:
            return
        ticket = await self._get_ticket()
        if ticket:
            await sender(self.user, ticket, status)

    async def _keepalive(self):
        while True:
            await asyncio.sleep(KEEPALIVE_SECONDS)
            try:
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
        self.ticket_expires = 0
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
            self.started = False
            # Reserve a reference immediately; network work runs beside input delivery.
            self.task = asyncio.create_task(self._run())
            return True

    async def _run(self):
        try:
            if not await self._get_ticket():
                return
            # A cancelled send may have reached WeChat, so stop it best-effort too.
            self.started = True
            await self._set_status(1)
            log_event("wechat.typing_started", level="debug", component="wechat")
            await self._keepalive()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            await self._failed(error, "start")

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
            if not self.started:
                return
            self.started = False
            try:
                await self._set_status(2)
            except Exception as error:
                await self._failed(error, "stop")
                return
            log_event("wechat.typing_stopped", level="debug", component="wechat")
