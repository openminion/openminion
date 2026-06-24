from collections.abc import Mapping
from typing import Any

from ..runtime import RuntimeContext


def emit_family_event(
    ctx: Any,
    *,
    event: str,
    payload: Mapping[str, Any] | dict[str, Any] | None = None,
) -> None:
    """Emit a family-level audit event via RuntimeContext.write_audit_event."""
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
    """Emit a provider/backend attempt event with attempt_index merged in."""
    merged: dict[str, Any] = {"attempt_index": attempt_index}
    if payload:
        merged.update(payload)
    emit_family_event(ctx, event=event, payload=merged)
