"""Shared turn-activity ledger model for non-widget status surfaces."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

KIND_TOOL = "tool"
KIND_SEARCH = "search"
KIND_PLAN = "plan"
KIND_APPROVAL = "approval"
KIND_BACKGROUND = "background"
KIND_STATUS = "status"
KIND_BUDGET = "budget"
KIND_ERROR = "error"
KIND_SUMMARY = "summary"

ACTIVITY_KINDS: frozenset[str] = frozenset(
    {
        KIND_TOOL,
        KIND_SEARCH,
        KIND_PLAN,
        KIND_APPROVAL,
        KIND_BACKGROUND,
        KIND_STATUS,
        KIND_BUDGET,
        KIND_ERROR,
        KIND_SUMMARY,
    }
)

STATE_STARTED = "started"
STATE_RUNNING = "running"
STATE_COMPLETED = "completed"
STATE_FAILED = "failed"
STATE_DENIED = "denied"
STATE_SKIPPED = "skipped"
STATE_BLOCKED = "blocked"
STATE_SUMMARY = "summary"

ACTIVITY_STATES: frozenset[str] = frozenset(
    {
        STATE_STARTED,
        STATE_RUNNING,
        STATE_COMPLETED,
        STATE_FAILED,
        STATE_DENIED,
        STATE_SKIPPED,
        STATE_BLOCKED,
        STATE_SUMMARY,
    }
)


_SEARCH_TOOL_NAME_PREFIXES: tuple[str, ...] = (
    "web.search",
    "web_search",
    "search.",
    "tinyfish.search",
    "serper",
    "firecrawl.search",
)


@dataclass(frozen=False)
class TurnActivityEvent:
    """Shell-neutral turn-activity row.

    Defaults let adapters render partial runtime payloads without raising.
    """

    kind: str = KIND_STATUS
    state: str = STATE_RUNNING
    title: str = ""
    detail: str = ""
    tool_name: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    call_id: str = ""
    duration_ms: int | None = None
    content: str = ""
    content_type: str = ""
    hidden_line_count: int = 0
    provenance: dict[str, Any] = field(default_factory=dict)
    fallback: dict[str, Any] = field(default_factory=dict)
    plan: dict[str, Any] = field(default_factory=dict)
    effort_level: str = ""
    tokens_delta: int | None = None
    source_payload: dict[str, Any] = field(default_factory=dict)
    timestamp_ms: int | None = None


def _coerce_optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _coerce_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _coerce_list(value: Any) -> list[Any]:
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


def _is_search_tool_name(tool_name: str) -> bool:
    name = (tool_name or "").strip().lower()
    if not name:
        return False
    return any(name.startswith(prefix) for prefix in _SEARCH_TOOL_NAME_PREFIXES)


def _provenance_from_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "model_tool_name",
        "runtime_tool_name",
        "runtime_binding_id",
        "runtime_resolution_source",
    )
    return {key: _coerce_str(payload.get(key, "")) for key in keys}


def _fallback_from_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "runtime_fallback_used": bool(payload.get("runtime_fallback_used", False)),
        "runtime_fallback_chain": _coerce_list(
            payload.get("runtime_fallback_chain", [])
        ),
        "fallback_index": _coerce_optional_int(payload.get("fallback_index")) or 0,
    }


def _tool_state_from_payload(*, kind: str, payload: Mapping[str, Any]) -> str:
    explicit_state = _coerce_str(payload.get("state", "")).strip().lower()
    if kind == "tool_started":
        return STATE_RUNNING if explicit_state in {"", "running"} else explicit_state
    if kind == "tool_completed":
        if explicit_state in {"denied"}:
            return STATE_DENIED
        ok = bool(payload.get("ok", False))
        return STATE_COMPLETED if ok else STATE_FAILED
    return STATE_RUNNING


def activity_from_progress_payload(
    payload: Mapping[str, Any] | None,
) -> TurnActivityEvent | None:
    """Map an existing runtime progress payload to a `TurnActivityEvent`.

    Returns `None` if the payload is empty / non-mapping or carries
    no recognizable activity content. Caller is responsible for
    routing the returned event to the surface-native renderer.
    """
    if not isinstance(payload, Mapping):
        return None
    payload_dict = dict(payload)
    kind_raw = _coerce_str(payload_dict.get("kind", "")).strip()
    if kind_raw in {"tool_started", "tool_completed"}:
        tool_name = _coerce_str(payload_dict.get("tool_name", "")).strip()
        kind = KIND_SEARCH if _is_search_tool_name(tool_name) else KIND_TOOL
        return TurnActivityEvent(
            kind=kind,
            state=_tool_state_from_payload(kind=kind_raw, payload=payload_dict),
            title=tool_name,
            tool_name=tool_name,
            args=_coerce_dict(payload_dict.get("args")),
            call_id=_coerce_str(payload_dict.get("call_id", "")),
            duration_ms=_coerce_optional_int(payload_dict.get("duration_ms")),
            content=_coerce_str(payload_dict.get("content", "")),
            provenance=_provenance_from_payload(payload_dict),
            fallback=_fallback_from_payload(payload_dict),
            effort_level=_coerce_str(payload_dict.get("effort_level", "")),
            tokens_delta=_coerce_optional_int(payload_dict.get("tokens_delta")),
            source_payload=payload_dict,
            timestamp_ms=_coerce_optional_int(payload_dict.get("timestamp_ms")),
        )
    if kind_raw == "budget_event":
        event_type = _coerce_str(payload_dict.get("event_type", "budget")) or "budget"
        return TurnActivityEvent(
            kind=KIND_BUDGET,
            state=STATE_SUMMARY,
            title=event_type,
            detail=_coerce_str(payload_dict.get("detail", "")),
            source_payload=payload_dict,
            timestamp_ms=_coerce_optional_int(payload_dict.get("timestamp_ms")),
        )
    if kind_raw in {"task_plan", "task_plan_revision", "task_plan_completed"}:
        plan = _coerce_dict(payload_dict.get("plan")) or payload_dict
        state = STATE_COMPLETED if kind_raw == "task_plan_completed" else STATE_RUNNING
        return TurnActivityEvent(
            kind=KIND_PLAN,
            state=state,
            title=_coerce_str(plan.get("summary", "")) or "Plan",
            plan=plan,
            source_payload=payload_dict,
            timestamp_ms=_coerce_optional_int(payload_dict.get("timestamp_ms")),
        )
    if kind_raw in {"task_plan_step_completed", "task_plan_step_blocked"}:
        blocked = kind_raw == "task_plan_step_blocked"
        return TurnActivityEvent(
            kind=KIND_PLAN,
            state=STATE_BLOCKED if blocked else STATE_COMPLETED,
            title=_coerce_str(payload_dict.get("step_text", "")) or "Step",
            detail=_coerce_str(payload_dict.get("reason" if blocked else "note", "")),
            source_payload=payload_dict,
        )
    if kind_raw in {"approval_request", "approval_decision"}:
        decision = _coerce_str(payload_dict.get("decision", "")).lower()
        state = {
            "denied": STATE_DENIED,
            "deny": STATE_DENIED,
            "allowed": STATE_COMPLETED,
            "allow": STATE_COMPLETED,
            "approved": STATE_COMPLETED,
        }.get(decision, STATE_RUNNING)
        return TurnActivityEvent(
            kind=KIND_APPROVAL,
            state=state,
            title=_coerce_str(payload_dict.get("tool_name", "")) or "approval",
            tool_name=_coerce_str(payload_dict.get("tool_name", "")),
            args=_coerce_dict(payload_dict.get("args")),
            detail=_coerce_str(payload_dict.get("reason", "")),
            source_payload=payload_dict,
        )
    if kind_raw in {"background_started", "background_completed"}:
        state = STATE_COMPLETED if kind_raw == "background_completed" else STATE_RUNNING
        return TurnActivityEvent(
            kind=KIND_BACKGROUND,
            state=state,
            title=_coerce_str(payload_dict.get("title", "")) or "background",
            detail=_coerce_str(payload_dict.get("detail", "")),
            source_payload=payload_dict,
            duration_ms=_coerce_optional_int(payload_dict.get("duration_ms")),
        )
    if kind_raw == "error":
        return TurnActivityEvent(
            kind=KIND_ERROR,
            state=STATE_FAILED,
            title=_coerce_str(payload_dict.get("title", "")) or "error",
            detail=_coerce_str(payload_dict.get("message", "")),
            source_payload=payload_dict,
        )
    if not payload_dict:
        return None
    return TurnActivityEvent(
        kind=KIND_STATUS,
        state=STATE_RUNNING,
        title=_coerce_str(payload_dict.get("label", ""))
        or _coerce_str(payload_dict.get("status_key", "")),
        detail=_coerce_str(payload_dict.get("detail_text", "")),
        source_payload=payload_dict,
        timestamp_ms=_coerce_optional_int(payload_dict.get("timestamp_ms")),
    )


def _tool_state_for_formatter(state: str) -> str:
    return {
        STATE_STARTED: "running",
        STATE_RUNNING: "running",
        STATE_COMPLETED: "ok",
        STATE_FAILED: "error",
        STATE_DENIED: "denied",
    }.get(state, state or "running")


def _todo_write_plan_from_args(args: Mapping[str, Any]) -> dict[str, Any] | None:
    todos = args.get("todos")
    if not isinstance(todos, list):
        return None
    items: list[dict[str, str]] = []
    for item in todos:
        if not isinstance(item, Mapping):
            continue
        text = _coerce_str(item.get("text", "")).strip()
        if not text:
            continue
        status = _coerce_str(item.get("status", "todo")).strip() or "todo"
        items.append({"text": text, "status": status})
    done = sum(1 for item in items if item.get("status") == "done")
    in_progress = sum(1 for item in items if item.get("status") == "in_progress")
    return {
        "items": items,
        "summary": f"{done}/{len(items)} done, {in_progress} in progress",
    }


def format_per_action_metrics_suffix(
    event: TurnActivityEvent | None,
) -> str:
    if event is None:
        return ""
    parts: list[str] = []
    tokens_delta = event.tokens_delta
    if isinstance(tokens_delta, int) and tokens_delta > 0:
        parts.append(f"↓ {tokens_delta} tokens")
    effort = (event.effort_level or "").strip()
    if effort:
        parts.append(f"thinking with {effort} effort")
    return f"({' · '.join(parts)})" if parts else ""


def format_activity_line(event: TurnActivityEvent | None) -> str | None:
    """Render an activity event as a plain-text line.

    Returns `None` for surfaces that should not render the event
    (empty/null event, or kinds the formatter has no canonical
    plain-text rendering for in v1).
    """
    if event is None:
        return None

    if event.kind in {KIND_TOOL, KIND_SEARCH}:
        if event.tool_name == "todo.write":
            plan = _todo_write_plan_from_args(event.args or {})
            if plan is not None:
                from openminion.cli.presentation.plan_render import render_plan

                return render_plan(plan)
        if event.kind == KIND_SEARCH:
            rendered = _format_search_activity_line(event)
            if rendered and not format_per_action_metrics_suffix(event):
                return rendered

        from openminion.cli.status.tool_calls import format_tool_call_line

        provenance = event.provenance or {}
        fallback = event.fallback or {}
        runtime_tool_name = _coerce_str(provenance.get("runtime_tool_name", ""))
        canonical = (
            _coerce_str(provenance.get("model_tool_name", "")) or event.tool_name
        )
        family_has_multiple_providers = bool(
            runtime_tool_name and runtime_tool_name != canonical
        )
        base_line = format_tool_call_line(
            tool_name=canonical or event.tool_name,
            args=event.args or {},
            state=_tool_state_for_formatter(event.state),
            duration_ms=event.duration_ms,
            model_tool_name=canonical or event.tool_name,
            runtime_tool_name=runtime_tool_name,
            runtime_fallback_used=bool(fallback.get("runtime_fallback_used", False)),
            runtime_fallback_chain=_coerce_list(
                fallback.get("runtime_fallback_chain", [])
            ),
            family_has_multiple_providers=family_has_multiple_providers,
        )
        suffix = format_per_action_metrics_suffix(event)
        return f"{base_line} {suffix}" if suffix else base_line

    if event.kind == KIND_PLAN:
        from openminion.cli.presentation.plan_render import render_plan

        if event.plan:
            return render_plan(event.plan)
        state_label = event.state.lower()
        title = event.title or "step"
        if state_label == STATE_COMPLETED:
            return f"Plan step done: {title}"
        if state_label == STATE_BLOCKED:
            return f"Plan step blocked: {title}{_detail_suffix(event.detail)}"
        return f"Plan step: {title}"

    if event.kind == KIND_APPROVAL:
        state_label = event.state.lower()
        tool_name = event.tool_name or event.title or "tool"
        if state_label == STATE_DENIED:
            return f"Approval denied: {tool_name}{_detail_suffix(event.detail)}"
        if state_label == STATE_COMPLETED:
            return f"Approval allowed: {tool_name}"
        return f"Approval requested: {tool_name}"

    if event.kind == KIND_BACKGROUND:
        title = event.title or "background"
        if event.state == STATE_COMPLETED:
            duration = (
                f" ({event.duration_ms} ms)" if event.duration_ms is not None else ""
            )
            return f"Background done: {title}{duration}"
        return f"Background: {title}"

    if event.kind == KIND_BUDGET:
        title = event.title or "budget"
        if title.startswith("budget."):
            title = title.split(".", 1)[1]
        return f"Budget: {title}"

    if event.kind == KIND_ERROR:
        title = event.title or "error"
        return f"Error: {title}{_detail_suffix(event.detail)}"

    if event.kind == KIND_STATUS:
        title = (event.title or "").strip()
        if not title:
            return None
        return title

    if event.kind == KIND_SUMMARY:
        title = event.title or "summary"
        return title

    return None


def _detail_suffix(detail: str) -> str:
    return f" — {detail}" if detail else ""


def _format_search_activity_line(event: TurnActivityEvent) -> str:
    query = (
        _coerce_str(event.args.get("query", ""))
        or _coerce_str(event.args.get("q", ""))
        or _coerce_str(event.args.get("pattern", ""))
    )
    title = event.title or event.tool_name or "Web Search"
    display_title = "Web Search" if "search" in title.lower() else title
    canonical = (event.tool_name or "").strip()
    tool_label = (
        f"{display_title} [{canonical}]"
        if canonical and canonical != display_title
        else display_title
    )
    if event.state in {STATE_RUNNING, STATE_STARTED}:
        quoted = f'("{query}")' if query else "()"
        return f"⏳ {tool_label}{quoted} searching…"
    count = _coerce_optional_int(event.source_payload.get("search_count")) or 1
    duration = ""
    if event.duration_ms is not None:
        seconds = max(0.0, float(event.duration_ms) / 1000.0)
        duration = f" in {seconds:.1f}s"
    query_suffix = f"({query!r})" if query else "()"
    if event.state == STATE_COMPLETED:
        plural = "es" if count != 1 else ""
        return f"● {tool_label}{query_suffix}\n└ Did {count} search{plural}{duration}"
    return f"✗ {tool_label}{query_suffix} failed{duration}"


@dataclass(frozen=True)
class CollapsedOutput:
    visible_lines: tuple[str, ...]
    hidden_line_count: int
    truncated: bool
    expand_hint: str


_DEFAULT_EXPAND_LABEL = "use /expand to see all"
_DEFAULT_EMPTY_PLACEHOLDER = "(no output)"


def collapse_output(
    content: str,
    *,
    max_lines: int,
    expand_label: str = _DEFAULT_EXPAND_LABEL,
    empty_placeholder: str = _DEFAULT_EMPTY_PLACEHOLDER,
) -> CollapsedOutput:
    cap = max(0, int(max_lines))
    body = (content or "").rstrip("\n")
    if not body:
        body = empty_placeholder
    lines = body.split("\n") if body else [empty_placeholder]
    if cap >= len(lines):
        return CollapsedOutput(
            visible_lines=tuple(lines),
            hidden_line_count=0,
            truncated=False,
            expand_hint="",
        )
    visible = tuple(lines[:cap])
    hidden = len(lines) - cap
    hint = f"… +{hidden} lines ({expand_label})"
    return CollapsedOutput(
        visible_lines=visible,
        hidden_line_count=hidden,
        truncated=True,
        expand_hint=hint,
    )


__all__ = [
    "CollapsedOutput",
    "collapse_output",
    "KIND_TOOL",
    "KIND_SEARCH",
    "KIND_PLAN",
    "KIND_APPROVAL",
    "KIND_BACKGROUND",
    "KIND_STATUS",
    "KIND_BUDGET",
    "KIND_ERROR",
    "KIND_SUMMARY",
    "ACTIVITY_KINDS",
    "STATE_STARTED",
    "STATE_RUNNING",
    "STATE_COMPLETED",
    "STATE_FAILED",
    "STATE_DENIED",
    "STATE_SKIPPED",
    "STATE_BLOCKED",
    "STATE_SUMMARY",
    "ACTIVITY_STATES",
    "TurnActivityEvent",
    "activity_from_progress_payload",
    "format_activity_line",
    "format_per_action_metrics_suffix",
]
