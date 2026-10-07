from collections.abc import Callable
from typing import Any, Mapping

from ..models import ToolEvent


def coerce_optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def build_tool_event_from_progress(
    payload: Mapping[str, Any],
    *,
    normalize_args: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> ToolEvent:
    tool_name = str(payload.get("tool_name", "") or "").strip() or "tool"
    raw_args = dict(payload.get("args", {}) or {})
    args = normalize_args(raw_args) if normalize_args is not None else raw_args
    content = str(payload.get("content", "") or "")
    fallback_chain_raw = payload.get("runtime_fallback_chain")
    fallback_chain = (
        list(fallback_chain_raw)
        if isinstance(fallback_chain_raw, (list, tuple))
        else None
    )
    return ToolEvent(
        tool_name=tool_name,
        args=args,
        content=content,
        content_type=str(payload.get("content_type", "") or "text"),
        duration_ms=coerce_optional_int(payload.get("duration_ms")),
        exit_code=coerce_optional_int(payload.get("exit_code")),
        truncated=bool(payload.get("truncated", False)),
        full_content=content,
        call_id=str(payload.get("call_id", "") or "").strip(),
        state=str(payload.get("state", "") or "").strip(),
        model_tool_name=str(payload.get("model_tool_name", "") or "").strip(),
        runtime_tool_name=str(payload.get("runtime_tool_name", "") or "").strip(),
        runtime_binding_id=str(payload.get("runtime_binding_id", "") or "").strip(),
        runtime_fallback_used=bool(payload.get("runtime_fallback_used", False)),
        runtime_fallback_chain=fallback_chain,
        runtime_resolution_source=str(
            payload.get("runtime_resolution_source", "") or ""
        ).strip(),
        fallback_index=coerce_optional_int(payload.get("fallback_index")),
    )


__all__ = ("build_tool_event_from_progress", "coerce_optional_int")
