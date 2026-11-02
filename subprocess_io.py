"""Bounded subprocess I/O; cancellation is propagated only after pipe cleanup."""
import asyncio


async def _finish(process, exchange, grace):
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
    if not exchange.done():
        exchange.cancel()
    await asyncio.gather(exchange, return_exceptions=True)
    if process.stdin is not None:
        process.stdin.close()
        try:
            await process.stdin.wait_closed()
        except (BrokenPipeError, ConnectionResetError):
            pass


async def communicate(process, data=None, *, timeout, grace=2):
    exchange = asyncio.create_task(process.communicate(data))
    try:
        return await asyncio.wait_for(asyncio.shield(exchange), timeout)
    finally:
        # A second cancellation must not abandon a Proactor pipe or child either.
        cleanup = asyncio.create_task(_finish(process, exchange, grace))
        cancelled = False
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                cancelled = True
        cleanup.result()
        if cancelled:
            raise asyncio.CancelledError()
