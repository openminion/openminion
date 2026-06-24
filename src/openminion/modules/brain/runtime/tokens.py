from typing import Any

from ..diagnostics.events import CanonicalEventLogger
from ..schemas import WorkingState


def estimate_tokens(*, llm_api: Any | None, model: str, context: dict[str, Any]) -> int:
    if llm_api is None:
        return 0
    try:
        return max(0, int(llm_api.estimate_tokens(model=model, context=context)))
    except Exception:  # noqa: BLE001
        return 0


def debit_tokens(
    *,
    state: WorkingState,
    response: dict[str, Any],
    logger: CanonicalEventLogger,
) -> None:
    usage = response.get("usage")
    tokens_used = 0
    if isinstance(usage, dict):
        maybe_total = usage.get("total_tokens")
        if isinstance(maybe_total, int):
            tokens_used = maybe_total
        logger.emit("llm.response", {"usage": usage}, trace_id=state.trace_id)
    if tokens_used <= 0:
        return
    state.budgets_remaining.tokens = max(
        0, state.budgets_remaining.tokens - tokens_used
    )
