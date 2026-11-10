"""Turn record construction and result classification for Codex backend events."""
import asyncio
import time

from .questions import async_questions
from .records import TurnResult

TURN_ERROR_PROMPTS = {
    "usageLimitExceeded": "TURN_FAILED_USAGE_LIMIT",
    "sessionBudgetExceeded": "TURN_FAILED_SESSION_BUDGET",
    "contextWindowExceeded": "TURN_FAILED_CONTEXT",
    "rateLimitExceeded": "TURN_FAILED_RATE_LIMIT",
    "unauthorized": "TURN_FAILED_UNAUTHORIZED",
    "modelNotFound": "TURN_FAILED_MODEL_NOT_FOUND",
    "flexUnavailable": "TURN_FAILED_SERVICE_UNAVAILABLE",
    "serverOverloaded": "TURN_FAILED_SERVICE_UNAVAILABLE",
    "policyCapacityUnavailable": "TURN_FAILED_SERVICE_UNAVAILABLE",
    "cyberPolicy": "TURN_FAILED_POLICY",
    "misalignmentPolicyViolation": "TURN_FAILED_POLICY",
    "tooManyDenials": "TURN_FAILED_TOO_MANY_DENIALS",
    "sandboxError": "TURN_FAILED_SANDBOX",
    "httpConnectionFailed": "TURN_FAILED_SERVICE_UNAVAILABLE",
}

HTTP_ERROR_VARIANTS = (
    "httpConnectionFailed",
    "http_connection_failed",
    "responseStreamConnectionFailed",
    "response_stream_connection_failed",
    "responseStreamDisconnected",
    "response_stream_disconnected",
    "responseTooManyFailedAttempts",
    "response_too_many_failed_attempts",
)


def new_turn(thread_id, turn_id, *, owned, lease, cwd):
    return {"thread": thread_id, "turn": turn_id, "messages": [], "owned": owned,
            "starting": False,
            "earlyEvents": [],
            "itemStartedAtMs": {},
            "last_progress_at": time.monotonic(),
            "lease": lease,
            "source": lease.source,
            "cwd": cwd,
            "done": asyncio.get_running_loop().create_future()}


def _insufficient_balance_message(message):
    if not isinstance(message, str):
        return False
    normalized = message.casefold()
    return ("insufficient balance" in normalized
            or "insufficient_quota" in normalized
            or ("402" in normalized and "payment required" in normalized))


def turn_error_code(error):
    if isinstance(error, str):
        return "usageLimitExceeded" if _insufficient_balance_message(error) else None
    if not isinstance(error, dict):
        return None
    info = error.get("codexErrorInfo")
    if info is None:
        info = error.get("codex_error_info")
    if isinstance(info, str) and info:
        return info
    if isinstance(info, dict):
        for name in HTTP_ERROR_VARIANTS:
            detail = info.get(name)
            if not isinstance(detail, dict):
                continue
            status = detail.get("httpStatusCode", detail.get("http_status_code"))
            if status == 402:
                return "usageLimitExceeded"
            if status == 429:
                return "rateLimitExceeded"
            if isinstance(status, int) and 500 <= status <= 599:
                return "serverOverloaded"
            return "httpConnectionFailed"
    if _insufficient_balance_message(error.get("message")):
        return "usageLimitExceeded"
    return None


def answer_text(current):
    messages = current["messages"]
    finals = [message for message in messages
              if async_questions(message) is None
              and message.get("phase") == "final_answer"]
    legacy = [message for message in messages
              if async_questions(message) is None and not message.get("phase")]
    return "\n\n".join(message["text"] for message in (finals or legacy))


def build_result(current, turn, *, prompt, error_code=None, usage=None):
    messages = current["messages"]
    finals = [message for message in messages
              if async_questions(message) is None
              and message.get("phase") == "final_answer"]
    legacy = [message for message in messages
              if async_questions(message) is None and not message.get("phase")]
    answer_items = finals or legacy
    text = "\n\n".join(message["text"] for message in answer_items)
    answer_item = answer_items[-1] if answer_items else {}
    has_partial_answer = any(
        async_questions(message) is None
        and message.get("phase") == "partial_answer"
        and message.get("text")
        for message in messages
    )
    status = turn["status"]
    if status != "completed":
        status_text = {
            "failed": prompt(TURN_ERROR_PROMPTS.get(error_code, "TURN_FAILED")),
            "interrupted": prompt("TURN_INTERRUPTED"),
        }.get(status, prompt("TURN_ENDED_UNEXPECTEDLY"))
        text = (text + "\n\n" if text else "") + status_text
    return TurnResult(
        current["source"], current["turn"],
        text if text or has_partial_answer else prompt("TURN_COMPLETED_EMPTY"),
        current["cwd"], status, tuple(current.get("requestIds", ())),
        final_answer_started_at_ms=answer_item.get("startedAtMs"),
        final_answer_completed_at_ms=answer_item.get("completedAtMs"),
        usage=usage,
    )
