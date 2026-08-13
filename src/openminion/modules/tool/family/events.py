from collections.abc import Mapping
from typing import Any

from ..runtime import RuntimeContext


def emit_family_event(
    ctx: Any,
    *,
    event: str,
    payload: Mapping[str, Any] | dict[str, Any] | None = None,
) -> None:
    if not isinstance(ctx, RuntimeContext):
        return
    try:
        ctx.write_audit_event({"event": event, **dict(payload or {})})
    except Exception:
        return


def emit_provider_attempt(
    ctx: Any,
    *,
    event: str,
    attempt_index: int,
    payload: dict[str, Any] | None = None,
) -> None:
    merged = {"attempt_index": attempt_index, **(payload or {})}
    emit_family_event(ctx, event=event, payload=merged)
