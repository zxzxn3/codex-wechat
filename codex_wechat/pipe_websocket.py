"""WebSocket over subprocess pipes, using websockets' public Sans-I/O API.

Windows Proactor supports subprocess pipes, but not asyncio Unix sockets.
The Codex proxy still owns local daemon discovery and authentication.
"""
import asyncio
from collections import deque

from websockets.client import ClientProtocol
from websockets.frames import Frame, Opcode
from websockets.http11 import Response
from websockets.protocol import OPEN
from websockets.uri import parse_uri


class PipeWebSocket:
    def __init__(self, process):
        self.process = process
        self.protocol = ClientProtocol(parse_uri("ws://localhost/"),
                                       max_size=16 * 1024 * 1024)
        self.frames = deque()
        self.send_lock = asyncio.Lock()
        self.fragment_type = None
        self.fragments = []
        self.closed = False
        self.handshake_complete = False

    async def flush(self):
        async with self.send_lock:
            for data in self.protocol.data_to_send():
                if data:
                    self.process.stdin.write(data)
            await self.process.stdin.drain()

    async def read_chunk(self):
        data = await self.process.stdout.read(65536)
        if not data:
            self.protocol.receive_eof()
            raise RuntimeError("共享连接已关闭")
        self.protocol.receive_data(data)
        for event in self.protocol.events_received():
            if isinstance(event, Response):
                if self.protocol.handshake_exc is not None:
                    raise RuntimeError("共享连接 WebSocket 握手失败")
                self.handshake_complete = True
            elif isinstance(event, Frame):
                self.frames.append(event)
        await self.flush()  # Also sends library-generated Pong/Close frames.
        if self.protocol.parser_exc is not None:
            raise RuntimeError("共享连接 WebSocket 数据无效")

    async def handshake(self):
        self.protocol.send_request(self.protocol.connect())
        await self.flush()
        async with asyncio.timeout(4):
            while not self.handshake_complete:
                await self.read_chunk()
        return self

    async def send(self, text):
        if self.closed:
            raise RuntimeError("共享连接已关闭")
        self.protocol.send_text(text.encode("utf-8"))
        await self.flush()

    async def recv(self):
        while not self.closed:
            if not self.frames:
                await self.read_chunk()
                continue
            frame = self.frames.popleft()
            if frame.opcode == Opcode.CLOSE:
                self.closed = True
                raise RuntimeError("共享连接已关闭")
            if frame.opcode in (Opcode.PING, Opcode.PONG):
                continue
            if frame.opcode in (Opcode.TEXT, Opcode.BINARY):
                self.fragment_type = frame.opcode
            self.fragments.append(frame.data)
            if len(self.fragments) > 16384:
                raise RuntimeError("共享连接 WebSocket 分片过多")
            if frame.fin:
                payload = b"".join(self.fragments)
                self.fragments.clear()
                return payload.decode("utf-8") if self.fragment_type == Opcode.TEXT else payload
        raise RuntimeError("共享连接已关闭")

    async def close(self):
        if self.closed:
            return
        self.closed = True
        if self.protocol.state == OPEN:
            try:
                self.protocol.send_close()
                await asyncio.wait_for(self.flush(), 1)
            except (TimeoutError, OSError, RuntimeError):
                pass
