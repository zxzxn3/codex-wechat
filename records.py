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


@dataclass(frozen=True)
class TurnResult:
    source: Source
    turn: str | None
    text: str
    cwd: str
    status: str

    @property
    def completed(self):
        return self.status == "completed"
