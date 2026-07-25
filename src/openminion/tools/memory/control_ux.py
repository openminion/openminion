"""Reusable explicit memory-control scenario for smoke and UX proof."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from openminion.modules.tool.contracts import ProviderToolCall
from openminion.modules.tool.base import ToolExecutionContext
from openminion.modules.tool.registry import ToolRegistry


@dataclass(frozen=True)
class MemoryControlScenario:
    scope: str
    record_type: str
    title: str
    content: dict[str, Any] | str
    search_query: str
    correction_title: str
    correction_content: dict[str, Any] | str
    forget_reason: str
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in (
            "scope",
            "record_type",
            "title",
            "search_query",
            "correction_title",
            "forget_reason",
        ):
            if not str(getattr(self, field_name) or "").strip():
                raise ValueError(f"{field_name} is required")
        if isinstance(self.content, str) and not self.content.strip():
            raise ValueError("content is required")
        if (
            isinstance(self.correction_content, str)
            and not self.correction_content.strip()
        ):
            raise ValueError("correction_content is required")


@dataclass(frozen=True)
class MemoryControlScenarioResult:
    original_record_id: str
    search_count: int
    search_record_ids: tuple[str, ...]
    correction_record_id: str
    forgotten_record_id: str
    forget_reason: str
    forget_deleted: bool
    audit_event_ids: tuple[str, ...]
    audit_event_types: tuple[str, ...]

    def as_payload(self) -> dict[str, Any]:
        return asdict(self)


def run_memory_control_scenario(
    *,
    registry: ToolRegistry,
    context: ToolExecutionContext,
    scenario: MemoryControlScenario,
) -> MemoryControlScenarioResult:
    """Run explicit write/search/correction/forget without inferring semantics."""

    original_id = _write_record(
        registry=registry,
        context=context,
        call_id="mcux-write-original",
        scope=scenario.scope,
        record_type=scenario.record_type,
        title=scenario.title,
        content=scenario.content,
        tags=list(scenario.tags),
    )
    search_payload = _search_records(
        registry=registry,
        context=context,
        query=scenario.search_query,
        scope=scenario.scope,
        record_type=scenario.record_type,
    )
    correction_content = _correction_payload(
        scenario.correction_content,
        corrects_record_id=original_id,
    )
    correction_id = _write_record(
        registry=registry,
        context=context,
        call_id="mcux-write-correction",
        scope=scenario.scope,
        record_type=scenario.record_type,
        title=scenario.correction_title,
        content=correction_content,
        tags=[*scenario.tags, "correction"],
    )
    forget_payload = _forget_record(
        registry=registry,
        context=context,
        record_id=original_id,
        reason=scenario.forget_reason,
    )
    audit_events = _memory_audit_events(context)
    return MemoryControlScenarioResult(
        original_record_id=original_id,
        search_count=int(search_payload.get("count", 0) or 0),
        search_record_ids=tuple(
            str(record.get("id", "") or "")
            for record in list(search_payload.get("records") or [])
            if str(record.get("id", "") or "").strip()
        ),
        correction_record_id=correction_id,
        forgotten_record_id=original_id,
        forget_reason=str(forget_payload.get("reason", "") or scenario.forget_reason),
        forget_deleted=bool(forget_payload.get("deleted")),
        audit_event_ids=tuple(
            str(event.get("event_id", "") or "")
            for event in audit_events
            if str(event.get("event_id", "") or "").strip()
        ),
        audit_event_types=tuple(
            str(event.get("event_type", "") or "")
            for event in audit_events
            if str(event.get("event_type", "") or "").strip()
        ),
    )


def format_memory_control_summary(result: MemoryControlScenarioResult) -> str:
    lines = [
        "memory control scenario complete",
        f"original_record_id: {result.original_record_id}",
        f"search_count: {result.search_count}",
        f"search_record_ids: {', '.join(result.search_record_ids) or 'none'}",
        f"correction_record_id: {result.correction_record_id}",
        f"forgotten_record_id: {result.forgotten_record_id}",
        f"forget_deleted: {str(result.forget_deleted).lower()}",
        f"forget_reason: {result.forget_reason}",
        f"audit_event_ids: {', '.join(result.audit_event_ids) or 'unavailable'}",
    ]
    return "\n".join(lines)


def _write_record(
    *,
    registry: ToolRegistry,
    context: ToolExecutionContext,
    call_id: str,
    scope: str,
    record_type: str,
    title: str,
    content: dict[str, Any] | str,
    tags: list[str],
) -> str:
    result = registry.execute_calls(
        [
            ProviderToolCall(
                name="memory.write",
                arguments={
                    "scope": scope,
                    "record_type": record_type,
                    "title": title,
                    "content": content,
                    "tags": tags,
                },
                id=call_id,
                source="memory-control-ux",
            )
        ],
        context=context,
    ).results[0]
    if not result.ok:
        raise RuntimeError(result.content or result.error)
    return str(result.data["record_id"])


def _search_records(
    *,
    registry: ToolRegistry,
    context: ToolExecutionContext,
    query: str,
    scope: str,
    record_type: str,
) -> dict[str, Any]:
    result = registry.execute_calls(
        [
            ProviderToolCall(
                name="memory.search",
                arguments={
                    "query": query,
                    "scopes": [scope],
                    "types": [record_type],
                    "limit": 5,
                },
                id="mcux-search-original",
                source="memory-control-ux",
            )
        ],
        context=context,
    ).results[0]
    if not result.ok:
        raise RuntimeError(result.content or result.error)
    return dict(result.data)


def _forget_record(
    *,
    registry: ToolRegistry,
    context: ToolExecutionContext,
    record_id: str,
    reason: str,
) -> dict[str, Any]:
    result = registry.execute_calls(
        [
            ProviderToolCall(
                name="memory.forget",
                arguments={"record_id": record_id, "reason": reason},
                id="mcux-forget-original",
                source="memory-control-ux",
            )
        ],
        context=context,
    ).results[0]
    if not result.ok:
        raise RuntimeError(result.content or result.error)
    return dict(result.data)


def _correction_payload(
    content: dict[str, Any] | str,
    *,
    corrects_record_id: str,
) -> dict[str, Any]:
    if isinstance(content, dict):
        payload = dict(content)
    else:
        payload = {"text": str(content)}
    payload["corrects_record_id"] = str(corrects_record_id)
    return payload


def _memory_audit_events(context: ToolExecutionContext) -> list[dict[str, Any]]:
    service = context.memory_service
    store = getattr(service, "_store", None)
    sink = getattr(store, "_sink", None)
    list_events = getattr(sink, "list_events", None)
    if callable(list_events):
        return [dict(event) for event in list_events()]
    events = getattr(sink, "events", None)
    if isinstance(events, list):
        return [_audit_event_payload(event) for event in events]
    return []


def _audit_event_payload(event: Any) -> dict[str, Any]:
    to_dict = getattr(event, "to_dict", None)
    if callable(to_dict):
        return dict(to_dict())
    if isinstance(event, dict):
        return dict(event)
    return {}


__all__ = [
    "MemoryControlScenario",
    "MemoryControlScenarioResult",
    "format_memory_control_summary",
    "run_memory_control_scenario",
]
