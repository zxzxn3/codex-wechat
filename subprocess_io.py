"""Await bounded child I/O and finish pipe cleanup before propagating cancellation."""
import asyncio

async def communicate(process, data=None, *, timeout, grace=2):
    exchange = asyncio.create_task(process.communicate(data))
    try:
        return await asyncio.wait_for(asyncio.shield(exchange), timeout)
    finally:
        if process.returncode is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), grace)
            except asyncio.TimeoutError:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()
        await asyncio.gather(exchange, return_exceptions=True)
        if process.stdin is not None:
            process.stdin.close()
            try:
                await process.stdin.wait_closed()
            except (BrokenPipeError, ConnectionResetError):
                pass
