from __future__ import annotations

from typing import Any, Callable, Optional

from openminion.api.runtime import APIRuntime
from openminion.modules.brain.diagnostics.status import PhaseStatus
import openminion.services.lifecycle.request_orchestrator as _request_orchestrator

TurnRequestError = _request_orchestrator.TurnRequestError
TurnTimeoutError = _request_orchestrator.TurnTimeoutError
_run_turn = _request_orchestrator.run_turn


def run_turn(
    config_path: Optional[str],
    payload: dict[str, Any],
    runtime: Optional[APIRuntime] = None,
    request_id: Optional[str] = None,
    progress_callback: Callable[[PhaseStatus], None] | None = None,
    approval_callback: Any | None = None,
) -> dict[str, Any]:
    """Run one turn through the canonical request orchestrator."""
    if runtime is not None:
        return runtime.run_turn(
            payload=payload,
            request_id=request_id,
            progress_callback=progress_callback,
            approval_callback=approval_callback,
        )

    return _run_turn(
        config_path=config_path,
        payload=payload,
        runtime_factory=APIRuntime.from_config_path,
        request_id=request_id,
        progress_callback=progress_callback,
        approval_callback=approval_callback,
    )


__all__ = [
    "TurnRequestError",
    "TurnTimeoutError",
    "run_turn",
]
