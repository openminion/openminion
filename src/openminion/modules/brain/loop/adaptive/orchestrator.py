from __future__ import annotations

from openminion.modules.brain.execution.loop_contracts import (
    ExecutionContext,
    ExecutionResult,
)
from openminion.modules.brain.execution.preflight import (
    ModePreparation,
    ValidationResult,
)

from .modes import ActLoopMode


def prepare_adaptive_loop(
    ctx: ExecutionContext,
    *,
    emit_status_updates: bool = False,
) -> ModePreparation:
    return ActLoopMode().prepare(ctx, emit_status_updates=emit_status_updates)


def validate_adaptive_loop(
    ctx: ExecutionContext,
    *,
    preparation: ModePreparation | None = None,
) -> ValidationResult | None:
    return ActLoopMode().validate(ctx, preparation=preparation)


def run_adaptive_loop(ctx: ExecutionContext) -> ExecutionResult:
    return ActLoopMode().execute(ctx)
