"""Shared helpers for memory reflection runtimes."""

from typing import Any

from ...diagnostics.events import CanonicalEventLogger
from ...schemas import WorkingState


def emit_skipped(
    *,
    logger: CanonicalEventLogger,
    event: str,
    state: WorkingState,
    reason: str,
    refs: list[str] | None = None,
    status: str = "info",
    **extra: Any,
) -> list[str]:
    logger.emit(
        event,
        {"reason": reason, **extra},
        trace_id=state.trace_id,
        status=status,
    )
    return list(refs or [])


def memory_barrel() -> Any:
    from . import base as memory_module

    return memory_module
