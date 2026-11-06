"""Contracts for independent error-handling axes and retry priority."""
import unittest

from codex_wechat.backend import DeferTurn, TurnStalled
from codex_wechat.codex_transport import CodexRpcError, SharedDaemonUnavailable
from codex_wechat.errors import (BackendStartupError, ErrorAction, UserFacingError,
                    WeChatApiError, WeChatAuthExpired, WeChatNotAcknowledged,
                    WeChatPollTimeout, WeChatTransportError, disposition,
                    writer_conflict)


class ErrorDispositionTests(unittest.TestCase):
    def assertDisposition(self, error, actions, *, retry_after=None, primary=None):
        result = disposition(error)
        self.assertEqual(result.actions, actions)
        self.assertEqual(result.retry_after, retry_after)
        if primary is not None:
            self.assertEqual(result.primary, primary)

    def test_dispositions_are_multi_axis_not_exclusive_states(self):
        self.assertDisposition(
            UserFacingError("fix it"),
            ErrorAction.USER_ACTION,
            primary=ErrorAction.USER_ACTION,
        )
        self.assertDisposition(
            BackendStartupError("unlock keyring", retry_after=30),
            ErrorAction.RETRYABLE,
            retry_after=30,
            primary=ErrorAction.RETRYABLE,
        )
        self.assertDisposition(
            SharedDaemonUnavailable(
                "shared unavailable",
                cause=BackendStartupError("retry", retry_after=12),
            ),
            ErrorAction.FALLBACK | ErrorAction.RETRYABLE,
            retry_after=12,
            primary=ErrorAction.FALLBACK,
        )
        self.assertDisposition(
            WeChatAuthExpired(),
            ErrorAction.USER_ACTION,
            primary=ErrorAction.USER_ACTION,
        )
        self.assertDisposition(WeChatPollTimeout(), ErrorAction.RETRYABLE)
        self.assertDisposition(WeChatTransportError(), ErrorAction.RETRYABLE)
        self.assertDisposition(WeChatNotAcknowledged(0), ErrorAction.USER_ACTION)
        self.assertDisposition(WeChatApiError(-1, "other"), ErrorAction.TERMINAL)
        self.assertDisposition(DeferTurn("wait"), ErrorAction.RETRYABLE)
        self.assertDisposition(TurnStalled("stalled"), ErrorAction.TERMINAL)
        self.assertDisposition(TimeoutError(), ErrorAction.RETRYABLE)

    def test_only_the_explicit_writer_conflict_is_retryable(self):
        conflict = CodexRpcError(-32600, "already has an active writer")
        self.assertTrue(writer_conflict(conflict))
        self.assertDisposition(
            conflict,
            ErrorAction.RETRYABLE | ErrorAction.USER_ACTION,
            primary=ErrorAction.RETRYABLE,
        )
        rejection = CodexRpcError(-32602, "invalid request")
        self.assertFalse(writer_conflict(rejection))
        self.assertDisposition(rejection, ErrorAction.TERMINAL)


if __name__ == "__main__":
    unittest.main()
