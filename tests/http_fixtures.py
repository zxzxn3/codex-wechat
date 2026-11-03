"""Protocol outcomes at the HTTP pool boundary, without opening connections."""
from unittest.mock import AsyncMock, patch
from latency import curl_timings


def exchange(output=b'{"ret":0}\n200', *, code=0, stderr=b''):
    return patch('wechat.PooledHTTP.request', AsyncMock(
        return_value=(output, dict(curl_timings(stderr), curl_code=code))))
