"""Self-awareness segment rendering helpers."""

from __future__ import annotations

from openminion.modules.context.schemas import BuildPackRequest
from openminion.modules.runtime.self_model import (
    SelfModelSnapshot,
    render_self_awareness_context_block,
)


def render_self_awareness_block(request: BuildPackRequest) -> str:
    payload = request.self_awareness
    if not isinstance(payload, dict) or not payload:
        return ""
    return render_self_awareness_context_block(
        SelfModelSnapshot.model_validate(payload)
    )


__all__ = ["render_self_awareness_block"]
