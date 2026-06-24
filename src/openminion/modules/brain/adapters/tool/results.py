"""Result shaping helpers for the brain tool adapter."""

import json
from collections.abc import Mapping
from typing import Any

from openminion.base.logging import get_logger
from openminion.modules.brain.constants import BRAIN_ACTION_STATUS_SUCCESS
from openminion.modules.tool import preferred_artifact_ref

_log = get_logger("brain.adapters.tool.runtime")


def _normalized_summary_token(value: Any, *, limit: int = 600) -> str:
    token = str(value or "").strip()
    if not token:
        return ""
    if len(token) <= limit:
        return token
    return token[:limit].rstrip() + "..."


def _derive_toolspec_summary(
    payload: Any, *, status: str, tool_name: str = "unknown"
) -> str:
    success = status == BRAIN_ACTION_STATUS_SUCCESS
    if not isinstance(payload, Mapping):
        return "Tool executed successfully" if success else "Tool execution failed"
    data_field = payload.get("data")
    outputs_field = payload.get("outputs")
    mappings: list[Mapping[str, Any]] = [payload]
    if isinstance(data_field, Mapping):
        mappings.append(data_field)
    if isinstance(outputs_field, Mapping):
        mappings.append(outputs_field)
    if success:
        for key in ("summary", "content", "message", "answer"):
            for mapping in mappings:
                token = _normalized_summary_token(mapping.get(key))
                if token:
                    return token
        synth_source: Any = None
        if isinstance(data_field, Mapping) and data_field:
            synth_source = data_field
        elif isinstance(payload, Mapping) and payload:
            synth_source = payload
        if synth_source:
            try:
                synthesized = _normalized_summary_token(
                    json.dumps(synth_source, sort_keys=True, default=str)
                )
            except Exception:
                synthesized = _normalized_summary_token(synth_source)
            if synthesized and synthesized not in {"{}", "[]"}:
                return synthesized
        _log.warning("tool.summary.generic_fallback tool=%s", tool_name)
        return "Tool executed successfully"
    raw_error = payload.get("error")
    if isinstance(raw_error, Mapping):
        error_message = _normalized_summary_token(
            raw_error.get("message") or raw_error.get("code")
        )
        if error_message:
            return error_message
    elif raw_error:
        error_message = _normalized_summary_token(raw_error)
        if error_message:
            return error_message
    for key in ("summary", "content", "message"):
        for mapping in mappings:
            token = _normalized_summary_token(mapping.get(key))
            if token:
                return token
    return "Tool execution failed"


def _normalized_artifact_refs(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    refs: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw:
        ref = preferred_artifact_ref(item)
        if not ref or ref in seen:
            continue
        seen.add(ref)
        refs.append({"ref": ref, "role": "output"})
    return refs


def _error_envelope(
    *,
    status: str,
    summary: str,
    code: str,
    message: str,
    latency_ms: int = 0,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return {
        "status": status,
        "summary": summary,
        "outputs": {},
        "artifact_refs": [],
        "memory_refs": [],
        "metrics": {
            "latency_ms": latency_ms,
            "tokens_used": 0,
            "cost_estimate": 0.0,
        },
        "error": error,
    }


__all__ = [
    "_derive_toolspec_summary",
    "_error_envelope",
    "_normalized_artifact_refs",
]
