"""Protocol outcomes at the HTTP pool boundary, without opening connections."""
from unittest.mock import AsyncMock, patch
from codex_wechat.latency import curl_timings


def exchange(output=b'{"ret":0}\n200', *, code=0, stderr=b''):
    return patch('codex_wechat.wechat.PooledHTTP.request', AsyncMock(
        return_value=(output, dict(curl_timings(stderr), curl_code=code))))
