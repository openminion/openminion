from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from openminion.base.types import Message

GatewayStreamEventKind = Literal[
    "assistant_token",
    "tool_call_started",
    "tool_call_completed",
    "budget_event",
    "final_message",
    "status",
]


class GatewayStreamEvent(BaseModel):
    """Canonical gateway-visible streaming event."""

    model_config = ConfigDict(extra="ignore")

    trace_id: str = Field(..., min_length=1)
    kind: GatewayStreamEventKind
    ts: str | None = None
    text: str | None = None
    tool_name: str | None = None
    args: dict[str, Any] | None = None
    call_id: str | None = None
    ok: bool | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    exit_code: int | None = None
    budget_event_type: str | None = None
    budget_payload: dict[str, Any] | None = None
    status_payload: dict[str, Any] | None = None
    final_message: dict[str, Any] | None = None
    model_tool_name: str | None = None
    runtime_tool_name: str | None = None
    runtime_binding_id: str | None = None
    runtime_fallback_used: bool | None = None
    runtime_fallback_chain: list[str] | None = None
    runtime_resolution_source: str | None = None
    fallback_index: int | None = None
    state: str | None = None
    tokens_delta: int | None = None
    effort_level: str | None = None


def _coerce_trace_id(payload: Mapping[str, Any], *, fallback: str | None = None) -> str:
    trace_id = str(payload.get("trace_id", "") or fallback or "").strip()
    return trace_id or "gateway-stream"


def _extract_provenance_fields(payload: Mapping[str, Any]) -> dict[str, Any]:
    """TESS-02: lift per-tool provenance + state from a progress payload.

    Returns a kwargs dict suitable for unpacking into `GatewayStreamEvent(...)`.
    Every field is optional and absent fields are omitted (not None'd) so
    Pydantic's default-None handling applies.
    """
    out: dict[str, Any] = {}
    model_tool_name = str(payload.get("model_tool_name", "") or "").strip()
    if model_tool_name:
        out["model_tool_name"] = model_tool_name
    runtime_tool_name = str(payload.get("runtime_tool_name", "") or "").strip()
    if runtime_tool_name:
        out["runtime_tool_name"] = runtime_tool_name
    runtime_binding_id = str(payload.get("runtime_binding_id", "") or "").strip()
    if runtime_binding_id:
        out["runtime_binding_id"] = runtime_binding_id
    if "runtime_fallback_used" in payload:
        out["runtime_fallback_used"] = bool(payload.get("runtime_fallback_used"))
    chain = payload.get("runtime_fallback_chain")
    if isinstance(chain, (list, tuple)):
        out["runtime_fallback_chain"] = [str(item) for item in chain if str(item)]
    runtime_resolution_source = str(
        payload.get("runtime_resolution_source", "") or ""
    ).strip()
    if runtime_resolution_source:
        out["runtime_resolution_source"] = runtime_resolution_source
    if "fallback_index" in payload:
        try:
            out["fallback_index"] = int(payload.get("fallback_index") or 0)
        except (TypeError, ValueError):
            pass
    state = str(payload.get("state", "") or "").strip()
    if state:
        out["state"] = state
    # per-action live metrics. Optional / default-safe.
    if "tokens_delta" in payload:
        try:
            tokens_delta = int(payload.get("tokens_delta") or 0)
            if tokens_delta > 0:
                out["tokens_delta"] = tokens_delta
        except (TypeError, ValueError):
            pass
    effort_level = str(payload.get("effort_level", "") or "").strip()
    if effort_level:
        out["effort_level"] = effort_level
    return out


def gateway_stream_event_from_progress(
    payload: Mapping[str, Any],
    *,
    trace_id: str | None = None,
    ts: str | None = None,
) -> GatewayStreamEvent | None:
    kind = str(payload.get("kind", "") or "").strip()
    resolved_trace_id = _coerce_trace_id(payload, fallback=trace_id)
    if kind == "tool_started":
        return GatewayStreamEvent(
            trace_id=resolved_trace_id,
            kind="tool_call_started",
            ts=ts,
            tool_name=str(payload.get("tool_name", "") or "").strip() or None,
            args=dict(payload.get("args", {}) or {}),
            call_id=str(payload.get("call_id", "") or "").strip() or None,
            **_extract_provenance_fields(payload),
        )
    if kind == "tool_completed":
        return GatewayStreamEvent(
            trace_id=resolved_trace_id,
            kind="tool_call_completed",
            ts=ts,
            tool_name=str(payload.get("tool_name", "") or "").strip() or None,
            args=dict(payload.get("args", {}) or {}),
            call_id=str(payload.get("call_id", "") or "").strip() or None,
            ok=bool(payload.get("ok", False)),
            duration_ms=_coerce_optional_int(payload.get("duration_ms")),
            exit_code=_coerce_optional_int(payload.get("exit_code")),
            text=str(payload.get("content", "") or "") or None,
            **_extract_provenance_fields(payload),
        )
    if kind == "budget_event":
        budget_payload = {
            key: value
            for key, value in dict(payload).items()
            if key not in {"kind", "trace_id"}
        }
        return GatewayStreamEvent(
            trace_id=resolved_trace_id,
            kind="budget_event",
            ts=ts,
            budget_event_type=str(payload.get("event_type", "") or "").strip() or None,
            budget_payload=budget_payload,
        )
    status_payload = dict(payload)
    if status_payload:
        return GatewayStreamEvent(
            trace_id=resolved_trace_id,
            kind="status",
            ts=ts,
            status_payload=status_payload,
        )
    return None


def gateway_stream_event_from_turn_chunk(
    chunk_payload: Mapping[str, Any],
) -> GatewayStreamEvent | None:
    kind = str(chunk_payload.get("kind", "") or "").strip()
    data = chunk_payload.get("data")
    payload = dict(data) if isinstance(data, Mapping) else {}
    trace_id = _coerce_trace_id(chunk_payload)
    ts = str(chunk_payload.get("ts", "") or "").strip() or None
    if kind in {"status", "tool_started", "tool_completed", "budget_event"}:
        event_payload = dict(payload)
        event_payload.setdefault("kind", kind)
        event_payload.setdefault("trace_id", trace_id)
        return gateway_stream_event_from_progress(
            event_payload,
            trace_id=trace_id,
            ts=ts,
        )
    if kind in {"token", "delta", "final_text"}:
        text = str(
            payload.get("text", "") or payload.get("delta_text", "") or ""
        ).strip()
        if not text:
            return None
        return GatewayStreamEvent(
            trace_id=trace_id,
            kind="assistant_token",
            ts=ts,
            text=text,
        )
    return None


def gateway_stream_event_from_message(
    message: Message,
    *,
    trace_id: str | None = None,
) -> GatewayStreamEvent:
    metadata = dict(getattr(message, "metadata", {}) or {})
    resolved_trace_id = (
        str(
            metadata.get("run_id") or metadata.get("trace_id") or trace_id or ""
        ).strip()
        or "gateway-stream"
    )
    return GatewayStreamEvent(
        trace_id=resolved_trace_id,
        kind="final_message",
        final_message={
            "channel": str(getattr(message, "channel", "") or ""),
            "target": str(getattr(message, "target", "") or ""),
            "body": str(getattr(message, "body", "") or ""),
            "metadata": metadata,
        },
    )


def _coerce_optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


__all__ = [
    "GatewayStreamEvent",
    "GatewayStreamEventKind",
    "gateway_stream_event_from_message",
    "gateway_stream_event_from_progress",
    "gateway_stream_event_from_turn_chunk",
]
