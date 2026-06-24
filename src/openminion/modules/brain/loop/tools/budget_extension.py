from __future__ import annotations

import time
from typing import Any

from openminion.modules.brain.config import ADAPTIVE_BUDGET_HARD_CAP
from openminion.modules.brain.constants import (
    STATE_KEY_MODULE_STATE,
    STOP_BUDGET_EXHAUSTED,
    STOP_HARD_CAP,
    STOP_NOOP_GUARD,
    STOP_SESSION_EXTENSIONS_EXHAUSTED,
    STOP_TOKEN_BUDGET_EXHAUSTED,
    STOP_USER_DECLINED,
    STOP_USER_TIMEOUT,
)
from openminion.modules.brain.schemas import AdaptiveBudgetConfig


BUDGET_MODULE_STATE_KEY = "adaptive_budget"
BUDGET_PENDING_KEY = "pending_extension"
BUDGET_APPROVED_KEY = "approved_extension"
BUDGET_SESSION_EXTENSIONS_KEY = "session_extensions_used"


def _budget_bucket(*, state: Any, create: bool = False) -> dict[str, Any] | None:
    module_state = getattr(state, STATE_KEY_MODULE_STATE, None)
    if not isinstance(module_state, dict):
        if not create:
            return None
        module_state = {}
        state.module_state = module_state
    bucket = (
        module_state.setdefault(BUDGET_MODULE_STATE_KEY, {})
        if create
        else module_state.get(BUDGET_MODULE_STATE_KEY)
    )
    return bucket if isinstance(bucket, dict) else None


def check_safety_rails(
    *,
    config: AdaptiveBudgetConfig,
    loop_state: Any,
    session_extensions_used: int,
    tokens_used: int,
    max_total_llm_tokens: int,
) -> str | None:
    """Return a typed stop reason when extension must halt."""
    extensions_used = int(getattr(loop_state, "extensions_used", 0) or 0)
    current_cap = int(getattr(loop_state, "effective_max_iterations", 0) or 0)
    consecutive_noops = int(getattr(loop_state, "consecutive_noops", 0) or 0)

    if extensions_used >= int(config.max_extensions_per_turn):
        return STOP_BUDGET_EXHAUSTED
    if session_extensions_used >= int(config.max_extensions_per_session):
        return STOP_SESSION_EXTENSIONS_EXHAUSTED
    if consecutive_noops >= int(config.max_adaptive_noops_per_turn):
        return STOP_NOOP_GUARD
    if max_total_llm_tokens > 0 and tokens_used > (0.9 * max_total_llm_tokens):
        return STOP_TOKEN_BUDGET_EXHAUSTED
    if current_cap >= ADAPTIVE_BUDGET_HARD_CAP:
        return STOP_HARD_CAP
    return None


def apply_extension(
    *,
    config: AdaptiveBudgetConfig,
    loop_state: Any,
) -> int:
    """Increase the effective iteration cap within the hard ceiling."""
    current_cap = int(getattr(loop_state, "effective_max_iterations", 0) or 0)
    new_cap = min(current_cap + int(config.extend_by), ADAPTIVE_BUDGET_HARD_CAP)
    loop_state.effective_max_iterations = new_cap
    loop_state.extensions_used = int(getattr(loop_state, "extensions_used", 0) or 0) + 1
    return new_cap


def compose_pause_question(
    *,
    config: AdaptiveBudgetConfig,
    loop_state: Any,
    active_work_summary: str = "",
    step_summaries: tuple[str, ...] = (),
    max_steps_hint: int | None = None,
) -> str:
    """AIB-10: build the ask-user message from typed state only."""
    iteration = int(getattr(loop_state, "iteration", 0) or 0)
    cap = int(getattr(loop_state, "effective_max_iterations", 0) or 0)

    lines: list[str] = [f"Budget reached: {iteration}/{cap} iterations"]
    summary = str(active_work_summary or "").strip()
    if summary:
        lines.append(f"Working on: {summary}")
    else:
        lines.append("Working on: (no active_work_summary)")

    if step_summaries:
        lines.append("Progress:")
        for step in step_summaries[-5:]:
            step_text = str(step or "").strip()
            if step_text:
                lines.append(f"  - {step_text[:200]}")

    if max_steps_hint is not None:
        try:
            remaining = max(0, int(max_steps_hint) - iteration)
        except (TypeError, ValueError):
            remaining = 0
        if remaining > 0:
            lines.append(f"Remaining estimate: ~{remaining} steps")

    extend_by = int(config.extend_by)
    lines.append("")
    lines.append(f"Continue for up to {extend_by} more iterations? (y/n)")
    return "\n".join(lines)


def mark_pending_extension(
    *,
    state: Any,
    cap_at_pause: int,
    extend_by: int,
    idle_timeout_s: int,
    clock: Any = time.time,
) -> dict[str, Any]:
    """Stamp pending-extension metadata on the working state."""
    expires_at = float(clock()) + max(0, int(idle_timeout_s))
    bucket = _budget_bucket(state=state, create=True)
    assert bucket is not None
    meta = {
        "cap_at_pause": int(cap_at_pause),
        "extend_by": int(extend_by),
        "expires_at": expires_at,
    }
    bucket[BUDGET_PENDING_KEY] = meta
    return meta


def clear_pending_extension(*, state: Any) -> dict[str, Any] | None:
    """Pop and return pending-extension metadata when present."""
    bucket = _budget_bucket(state=state)
    if bucket is None:
        return None
    return bucket.pop(BUDGET_PENDING_KEY, None)


def approve_pending_extension(
    *,
    state: Any,
    clock: Any = time.time,
) -> dict[str, Any] | None:
    """Move a pending interactive extension into one-shot approved state."""
    meta = clear_pending_extension(state=state)
    if not isinstance(meta, dict):
        return None
    try:
        cap_at_pause = int(meta.get("cap_at_pause", 0) or 0)
    except (TypeError, ValueError):
        cap_at_pause = 0
    try:
        extend_by = int(meta.get("extend_by", 0) or 0)
    except (TypeError, ValueError):
        extend_by = 0
    session_extensions_used = record_session_extension(state=state)
    approved = {
        "cap_at_pause": cap_at_pause,
        "extend_by": extend_by,
        "target_cap": cap_at_pause + max(0, extend_by),
        "approved_at": float(clock()),
        "session_extensions_used": session_extensions_used,
    }
    bucket = _budget_bucket(state=state, create=True)
    assert bucket is not None
    bucket[BUDGET_APPROVED_KEY] = approved
    return approved


def consume_approved_extension(*, state: Any) -> dict[str, Any] | None:
    """Pop a one-shot approved extension for adaptive profile construction."""
    bucket = _budget_bucket(state=state)
    if bucket is None:
        return None
    meta = bucket.pop(BUDGET_APPROVED_KEY, None)
    return meta if isinstance(meta, dict) else None


def get_pending_extension(*, state: Any) -> dict[str, Any] | None:
    """Peek the pending-extension metadata without clearing it."""
    bucket = _budget_bucket(state=state)
    if bucket is None:
        return None
    meta = bucket.get(BUDGET_PENDING_KEY)
    return meta if isinstance(meta, dict) else None


def is_pending_extension_expired(
    meta: dict[str, Any], *, clock: Any = time.time
) -> bool:
    """Return whether the pending-extension metadata has expired."""
    try:
        expires_at = float(meta.get("expires_at", 0) or 0)
    except (TypeError, ValueError):
        return False
    return expires_at > 0 and float(clock()) > expires_at


def record_session_extension(*, state: Any) -> int:
    """Increment and return the session-wide extension counter."""
    bucket = _budget_bucket(state=state, create=True)
    assert bucket is not None
    count = int(bucket.get(BUDGET_SESSION_EXTENSIONS_KEY, 0) or 0) + 1
    bucket[BUDGET_SESSION_EXTENSIONS_KEY] = count
    return count


def get_session_extensions_used(*, state: Any) -> int:
    """Read the session-wide extensions count. Zero when unset."""
    bucket = _budget_bucket(state=state)
    if bucket is None:
        return 0
    try:
        return int(bucket.get(BUDGET_SESSION_EXTENSIONS_KEY, 0) or 0)
    except (TypeError, ValueError):
        return 0


__all__ = [
    "ADAPTIVE_BUDGET_HARD_CAP",
    "BUDGET_APPROVED_KEY",
    "BUDGET_MODULE_STATE_KEY",
    "BUDGET_PENDING_KEY",
    "BUDGET_SESSION_EXTENSIONS_KEY",
    "STOP_BUDGET_EXHAUSTED",
    "STOP_HARD_CAP",
    "STOP_NOOP_GUARD",
    "STOP_SESSION_EXTENSIONS_EXHAUSTED",
    "STOP_TOKEN_BUDGET_EXHAUSTED",
    "STOP_USER_DECLINED",
    "STOP_USER_TIMEOUT",
    "approve_pending_extension",
    "apply_extension",
    "check_safety_rails",
    "clear_pending_extension",
    "compose_pause_question",
    "consume_approved_extension",
    "get_pending_extension",
    "get_session_extensions_used",
    "is_pending_extension_expired",
    "mark_pending_extension",
    "record_session_extension",
]
