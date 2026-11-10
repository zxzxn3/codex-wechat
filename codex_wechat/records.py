"""Small immutable records crossing the Codex/WeChat boundary."""
from dataclasses import dataclass
from enum import StrEnum


class InputPhase(StrEnum):
    PREPARING = "preparing"
    DISPATCHING = "dispatching"
    ACCEPTED = "accepted"
    UNCERTAIN = "uncertain"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class Source:
    backend: str
    connection: str
    selection: int
    thread: str | None


@dataclass(frozen=True)
class AgentMessage:
    source: Source
    turn: str
    item: str | None
    text: str
    cwd: str
    phase: str
    request_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class TurnResult:
    source: Source
    turn: str | None
    text: str
    cwd: str
    status: str
    request_ids: tuple[str, ...] = ()
    final_answer_started_at_ms: int | None = None
    final_answer_completed_at_ms: int | None = None
    usage: dict | None = None

    @property
    def completed(self):
        return self.status == "completed"


@dataclass(frozen=True)
class Delivered:
    """The channel accepted the payload and returned a message id."""

    message_id: str


@dataclass(frozen=True)
class AcceptedUnknown:
    """The channel accepted the request but did not confirm delivery."""

    ret: int | str | None = None


@dataclass(frozen=True)
class Rejected:
    """The channel definitively rejected this payload."""

    code: int | str
    kind: str


@dataclass(frozen=True)
class Retryable:
    """The request did not establish a definite channel outcome."""

    reason: str
    retry_after: float | None = None


@dataclass(frozen=True)
class AuthenticationRequired:
    """The channel credential was rejected and needs an interactive rebind."""


SendOutcome = Delivered | AcceptedUnknown | Rejected | Retryable | AuthenticationRequired
