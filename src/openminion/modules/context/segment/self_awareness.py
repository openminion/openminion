"""Self-awareness segment rendering helpers."""

from __future__ import annotations

from openminion.modules.context.schemas import BuildPackRequest
from openminion.modules.runtime.self_model import (
    SelfModelSnapshot,
    render_self_awareness_context_block,
)


def render_self_awareness_block(request: BuildPackRequest) -> str:
    payload = (
        dict(request.self_awareness) if isinstance(request.self_awareness, dict) else {}
    )
    if not payload:
        return ""
    snapshot = SelfModelSnapshot.model_validate(payload)
    return render_self_awareness_context_block(snapshot)


__all__ = ["render_self_awareness_block"]
