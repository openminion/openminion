from typing import TYPE_CHECKING, Any

from .runtime.turn import recursive as _recursive_runtime
from ..diagnostics.events import CanonicalEventLogger
from ..schemas import WorkingState

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..runner import BrainRunner


_RECURSIVE_EVENT_NAMES = (
    "brain.recursive_turn.started",
    "brain.recursive_turn.completed",
    "brain.recursive_turn.error",
    "brain.recursive_turn.blocked",
    "brain.recursive_turn.writeback_error",
)


def run_recursive_turn(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    user_input: str | None,
    logger: CanonicalEventLogger,
) -> Any:
    """Delegate to openminion-rlm for autonomous-mode turns."""
    return _recursive_runtime.run_recursive_turn(
        runner,
        state=state,
        user_input=user_input,
        logger=logger,
    )
