"""OpenMinion cli support for tool call format."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

MARKER_OK = "●"
MARKER_FAIL = "✗"
MARKER_RUNNING = "⏳"

_ARGS_PREVIEW_MAX = 120
_COMMAND_PREVIEW_MAX = 80
_QUERY_PREVIEW_MAX = 80
_FALLBACK_PREVIEW_MAX = 80


def format_tool_call_line(
    *,
    tool_name: str,
    args: Mapping[str, Any] | None = None,
    state: str = "ok",
    duration_ms: int | None = None,
    model_tool_name: str = "",
    runtime_tool_name: str = "",
    runtime_fallback_used: bool = False,
    runtime_fallback_chain: list[str] | None = None,
    family_has_multiple_providers: bool = False,
    extra_detail: str = "",
) -> str:
    marker = _state_marker(state)
    canonical_name = (model_tool_name or tool_name or "").strip() or "tool"
    args_preview = format_tool_args_preview(canonical_name, args or {})
    provenance = format_tool_provenance_marker(
        model_tool_name=canonical_name,
        runtime_tool_name=runtime_tool_name,
        family_has_multiple_providers=family_has_multiple_providers,
    )
    fallback = format_tool_fallback_marker(
        runtime_fallback_used=runtime_fallback_used,
        runtime_fallback_chain=runtime_fallback_chain,
    )
    duration_suffix = _format_duration(duration_ms)
    cleaned_detail = str(extra_detail or "").strip()
    detail_suffix = f" ({cleaned_detail})" if cleaned_detail else ""

    line = f"{marker} {canonical_name}({args_preview}){provenance}{fallback}"
    if state in {"denied", "error"} and cleaned_detail:
        line = f"{line} {state}{detail_suffix}"
        if duration_suffix:
            line = f"{line} {duration_suffix}"
        return line
    if state == "approving":
        return f"{line} approving…"
    if state == "running":
        return f"{line} {duration_suffix}" if duration_suffix else f"{line} running…"
    return f"{line} {duration_suffix}" if duration_suffix else line


def format_tool_args_preview(tool_name: str, args: Mapping[str, Any] | None) -> str:
    """Render an args preview per spec §7.2.

    Empty / missing args -> `""` (caller wraps with parentheses).
    """
    args_dict = dict(args or {})
    if not args_dict:
        return ""

    name = str(tool_name or "").strip().lower()

    if name.startswith("exec.") or name.startswith("git."):
        command = (
            str(args_dict.get("command", "") or "")
            or str(args_dict.get("cmd", "") or "")
            or _join_args_string(args_dict.get("args"))
        )
        if command:
            return _quote(_truncate_middle(command, _COMMAND_PREVIEW_MAX))

    if name in {"web.search", "code.grep", "code.symbol_find"}:
        query = (
            str(args_dict.get("query", "") or "")
            or str(args_dict.get("q", "") or "")
            or str(args_dict.get("pattern", "") or "")
        )
        if query:
            return _quote(_truncate_middle(query, _QUERY_PREVIEW_MAX))

    path = (
        str(args_dict.get("path", "") or "")
        or str(args_dict.get("file", "") or "")
        or str(args_dict.get("url", "") or "")
    )
    if path and (
        name.startswith("file.") or name.startswith("code.") or name == "web.fetch"
    ):
        return _quote(_short_path(path))

    try:
        compact = json.dumps(
            args_dict, sort_keys=True, default=str, separators=(",", ":")
        )
    except (TypeError, ValueError):
        compact = str(args_dict)
    truncated = _truncate_middle(compact, _COMMAND_PREVIEW_MAX)

    if len(truncated) > _ARGS_PREVIEW_MAX:
        truncated = truncated[: _ARGS_PREVIEW_MAX - 1] + "…"
    return truncated


def format_tool_provenance_marker(
    *,
    model_tool_name: str,
    runtime_tool_name: str,
    family_has_multiple_providers: bool,
) -> str:
    """Render the §7.3 provenance suffix.

    Returns empty string when:
    - the family has only one registered provider, OR
    - `runtime_tool_name` is empty, OR
    - `runtime_tool_name` doesn't differ from `model_tool_name`.
    """
    canonical = str(model_tool_name or "").strip()
    runtime = str(runtime_tool_name or "").strip()
    if not family_has_multiple_providers or not runtime or runtime == canonical:
        return ""
    short = _short_provider_id(runtime)
    return f" → {short}" if short else ""


def format_tool_fallback_marker(
    *,
    runtime_fallback_used: bool,
    runtime_fallback_chain: list[str] | None,
) -> str:
    """Render the §7.4 fallback marker.

    Returns empty when `runtime_fallback_used` is False.
    """
    if not runtime_fallback_used:
        return ""
    chain = list(runtime_fallback_chain or [])
    if not chain:
        return " (fallback)"
    first_attempted = str(chain[0] or "").strip()
    if not first_attempted:
        return " (fallback)"
    short = _short_provider_id(first_attempted)
    label = short or _truncate_middle(first_attempted, _FALLBACK_PREVIEW_MAX)
    return f" (fallback after {label})"


def _state_marker(state: str) -> str:
    normalized = str(state or "").strip().lower()
    if normalized in {"error", "denied"}:
        return MARKER_FAIL
    if normalized in {"running", "approving", "in_progress"}:
        return MARKER_RUNNING
    return MARKER_OK


def _format_duration(duration_ms: int | None) -> str:
    if duration_ms is None:
        return ""
    try:
        ms = int(duration_ms)
    except (TypeError, ValueError):
        return ""
    if ms < 0:
        return ""
    if ms < 1000:
        return f"{ms}ms"
    seconds = ms / 1000.0
    return f"{seconds:.1f}s"


def _quote(value: str) -> str:
    return f'"{value}"'


def _join_args_string(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return " ".join(str(item) for item in value)
    if value is None:
        return ""
    return str(value)


def _short_path(path: str) -> str:
    cleaned = str(path or "").strip()
    if not cleaned:
        return ""
    normalized = cleaned.replace("\\", "/").rstrip("/")
    segments = [s for s in normalized.split("/") if s]
    if len(segments) <= 2:
        return "/".join(segments) or cleaned
    return "/".join(segments[-2:])


def _truncate_middle(value: str, max_len: int) -> str:
    text = str(value or "")
    if len(text) <= max_len:
        return text
    if max_len <= 1:
        return text[:max_len]
    keep = max_len - 1
    head = keep // 2
    tail = keep - head
    return f"{text[:head]}…{text[-tail:]}" if tail > 0 else f"{text[:head]}…"


def _short_provider_id(runtime_tool_name: str) -> str:
    raw = str(runtime_tool_name or "").strip()
    if not raw:
        return ""
    segments = raw.split(".")
    if len(segments) >= 3:
        return segments[1]
    if len(segments) == 2:
        return segments[0]
    return raw


__all__ = [
    "MARKER_OK",
    "MARKER_FAIL",
    "MARKER_RUNNING",
    "format_tool_call_line",
    "format_tool_args_preview",
    "format_tool_provenance_marker",
    "format_tool_fallback_marker",
]
