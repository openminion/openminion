"""Turn-level deadline helpers and typed timeout metadata."""

import time
from dataclasses import dataclass

from openminion.services.agent.constants import (
    TERMINATION_REASON_TIME_BUDGET_EXCEEDED,
)

AR15_VOCABULARY_VALUE: str = TERMINATION_REASON_TIME_BUDGET_EXCEEDED


@dataclass(frozen=True)
class DeadlineState:
    started_at_ms: float
    max_elapsed_ms: int


def _now_ms() -> float:
    return time.monotonic() * 1000.0


def start_deadline(*, max_elapsed_ms: int) -> DeadlineState:
    return DeadlineState(started_at_ms=_now_ms(), max_elapsed_ms=int(max_elapsed_ms))


def is_deadline_exceeded(state: DeadlineState | None) -> bool:
    if state is None or state.max_elapsed_ms <= 0:
        return False
    return _now_ms() - state.started_at_ms >= float(state.max_elapsed_ms)


def elapsed_ms(state: DeadlineState | None) -> int:
    if state is None:
        return 0
    return max(0, int(_now_ms() - state.started_at_ms))


def build_time_budget_exceeded_metadata(state: DeadlineState | None) -> dict[str, str]:
    return {
        "tool_loop_termination_reason": AR15_VOCABULARY_VALUE,
        "elapsed_ms": str(elapsed_ms(state)),
        "time_budget_ms": str(state.max_elapsed_ms if state is not None else 0),
    }
