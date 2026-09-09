from dataclasses import dataclass, field
from typing import Any

from openminion.base.time import utc_now_iso

TURN_STREAM_SCHEMA_VERSION = "openminion.turn-stream.v1"


@dataclass(frozen=True)
class ToolCallSummary:
    name: str
    count: int = 1
    status: str = "unknown"
    duration_ms: int = 0


@dataclass(frozen=True)
class TurnTelemetry:
    tokens_in: int = 0
    tokens_out: int = 0
    duration_ms: int = 0
    retries: int = 0
    queue_wait_ms: int = 0


@dataclass(frozen=True)
class TurnError:
    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TurnResponse:
    final_text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    stats: dict[str, Any] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    tool_calls_summary: list[ToolCallSummary] = field(default_factory=list)
    memory_write_intents: list[dict[str, Any]] = field(default_factory=list)
    telemetry: TurnTelemetry = field(default_factory=TurnTelemetry)
    errors: list[TurnError] = field(default_factory=list)


@dataclass(frozen=True)
class TurnChunk:
    trace_id: str
    kind: str
    data: dict[str, Any] = field(default_factory=dict)
    ts: str = field(default_factory=utc_now_iso)
    schema_version: str = TURN_STREAM_SCHEMA_VERSION
    sequence: int = 0
    event_id: str = ""


@dataclass(frozen=True)
class TurnRequest:
    trace_id: str
    agent_id: str
    session_id: str
    input_text: str
    attachments: list[str] = field(default_factory=list)
    mode: str = "oneshot"
    stream: bool = False
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentStatus:
    agent_id: str
    created_at: str
    last_used_at: str
    queued_turns: int
    active_turns: int
    turns_handled: int


@dataclass(frozen=True)
class AgentHandle:
    agent_id: str


__all__ = [
    "AgentHandle",
    "AgentStatus",
    "ToolCallSummary",
    "TURN_STREAM_SCHEMA_VERSION",
    "TurnChunk",
    "TurnError",
    "TurnRequest",
    "TurnResponse",
    "TurnTelemetry",
]
