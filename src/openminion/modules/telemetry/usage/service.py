from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime
import json
from typing import Any

from .constants import RUNTIME_EVENT_READ_LIMIT
from .token_usage import (
    TokenUsageRecord,
    TokenUsageSummary,
    event_ref_from_session_event,
    records_from_session_event,
    sort_session_events,
)
from .types import (
    RunStats,
    RunStatsSummary,
    SessionStatsSummary,
    ToolCallCount,
)

_TOKEN_USAGE_EVENT_TYPES = frozenset(
    {"llm.call.completed", "context.manifest.created", "llm.cache.metrics"}
)


@dataclass(frozen=True)
class _EventReadResult:
    events: tuple[dict[str, Any], ...]
    complete: bool
    events_scanned: int
    event_limit: int | None


def _normalize_iso_datetime(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _usage_stats_from_payload(payload: dict[str, Any]) -> RunStats:
    usage = payload.get("usage")
    if isinstance(usage, dict):
        stats = RunStats.from_mapping(usage)
        if stats is not None:
            return RunStats(
                input_tokens=stats.input_tokens,
                output_tokens=stats.output_tokens,
                cache_read_tokens=stats.cache_read_tokens,
                llm_calls=1,
            )
    return RunStats(llm_calls=1)


def _tool_name_from_payload(payload: dict[str, Any]) -> str:
    for key in ("tool_name", "name"):
        normalized = str(payload.get(key, "") or "").strip()
        if normalized:
            return normalized
    request = payload.get("request")
    if isinstance(request, dict):
        for key in ("tool_name", "name"):
            normalized = str(request.get(key, "") or "").strip()
            if normalized:
                return normalized
    return ""


def _tool_error_delta(event_type: str, payload: dict[str, Any]) -> int:
    if event_type == "tool.call.blocked":
        return 1
    status = str(payload.get("status", "") or "").strip().lower()
    if status and status not in {"success", "completed", "ok"}:
        return 1
    error = payload.get("error")
    return 1 if error else 0


def _event_run_id(event: dict[str, Any]) -> str:
    payload = event.get("payload")
    if isinstance(payload, dict):
        return str(payload.get("run_id", "") or "").strip()
    return ""


def _event_trace_id(event: dict[str, Any]) -> str:
    return str(event.get("trace_id", "") or "").strip()


def _event_belongs_to_run(
    *,
    event: dict[str, Any],
    run_id: str,
    request_id: str,
) -> bool:
    event_run_id = _event_run_id(event)
    if event_run_id:
        return event_run_id == run_id
    if request_id:
        return _event_trace_id(event) == request_id
    return False


def _event_llm_call_id(event: dict[str, Any]) -> str:
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("llm_call_id", "") or "").strip()


def _is_run_usage_event(
    event: dict[str, Any],
    *,
    run_id: str,
    request_id: str,
    llm_call_ids: set[str],
) -> bool:
    event_type = str(event.get("event_type", "") or "").strip()
    if event_type not in _TOKEN_USAGE_EVENT_TYPES:
        return False
    if _event_belongs_to_run(event=event, run_id=run_id, request_id=request_id):
        return True
    return (
        event_type == "context.manifest.created"
        and _event_llm_call_id(event) in llm_call_ids
    )


def _normalize_event_limit(event_limit: int | None) -> int | None:
    if event_limit is None:
        return None
    normalized = int(event_limit)
    if normalized <= 0:
        raise ValueError("event_limit must be greater than zero")
    return normalized


def _normalize_session_event(event: Any) -> dict[str, Any]:
    if isinstance(event, dict):
        normalized = dict(event)
        normalized["event_id"] = str(
            normalized.get("event_id") or normalized.get("id") or ""
        )
        normalized["event_type"] = str(
            normalized.get("event_type") or normalized.get("type") or ""
        )
        normalized["timestamp"] = str(
            normalized.get("timestamp")
            or normalized.get("created_at")
            or normalized.get("ts")
            or ""
        )
        payload = normalized.get("payload")
        normalized["payload"] = payload if isinstance(payload, dict) else {}
        normalized["trace_id"] = str(normalized.get("trace_id") or "")
        return normalized
    return {
        "event_id": str(
            getattr(event, "event_id", None) or getattr(event, "id", "") or ""
        ),
        "event_type": str(getattr(event, "event_type", "") or ""),
        "timestamp": str(
            getattr(event, "timestamp", None)
            or getattr(event, "created_at", "")
            or ""
        ),
        "payload": getattr(event, "payload", {}) or {},
        "trace_id": str(getattr(event, "trace_id", "") or ""),
        "seq": getattr(event, "seq", None),
    }


def _is_tool_request_event(event_type: str) -> bool:
    return event_type in {"tool.request", "tool.requested", "tool.started"}


def _is_tool_result_event(event_type: str) -> bool:
    return event_type in {
        "tool.completed",
        "tool.failed",
        "tool.call.blocked",
    }


class StatsService:
    def __init__(self, store: Any) -> None:
        self._store = store

    def get_run_token_usage(
        self,
        run_id: str,
        *,
        event_limit: int | None = None,
    ) -> TokenUsageSummary | None:
        if not hasattr(self._store, "get_run_record"):
            return None
        record = self._store.get_run_record(run_id)
        if record is None:
            return None
        session_id = str(record.get("session_id", "") or "").strip()
        if not session_id:
            return None
        meta = record.get("meta")
        meta_map = dict(meta) if isinstance(meta, dict) else {}
        request_id = str(meta_map.get("request_id", "") or "").strip()
        read = self._read_session_events(session_id, event_limit=event_limit)
        direct_events = [
            event
            for event in read.events
            if _event_belongs_to_run(
                event=event,
                run_id=run_id,
                request_id=request_id,
            )
        ]
        llm_call_ids = {
            _event_llm_call_id(event)
            for event in direct_events
            if event.get("event_type") == "llm.call.completed"
            and _event_llm_call_id(event)
        }
        usage_events = [
            event
            for event in read.events
            if _is_run_usage_event(
                event,
                run_id=run_id,
                request_id=request_id,
                llm_call_ids=llm_call_ids,
            )
        ]
        return self._build_token_usage_summary(
            session_id=session_id,
            run_id=str(run_id),
            usage_events=usage_events,
            read=read,
        )

    def get_session_token_usage(
        self,
        session_id: str,
        *,
        event_limit: int | None = None,
    ) -> TokenUsageSummary:
        read = self._read_session_events(session_id, event_limit=event_limit)
        return self._build_token_usage_summary(
            session_id=session_id,
            usage_events=[
                event
                for event in read.events
                if event.get("event_type") in _TOKEN_USAGE_EVENT_TYPES
            ],
            read=read,
        )

    def get_run_stats(self, run_id: str) -> RunStatsSummary | None:
        if not hasattr(self._store, "get_run_record"):
            return None
        record = self._store.get_run_record(run_id)
        if record is None:
            return None
        session_id = str(record.get("session_id", "") or "").strip()
        if not session_id:
            return None
        meta = record.get("meta")
        meta_map = dict(meta) if isinstance(meta, dict) else {}
        request_id = str(meta_map.get("request_id", "") or "").strip()
        stats = RunStats(
            input_tokens=max(0, int(record.get("input_tokens") or 0)),
            output_tokens=max(0, int(record.get("output_tokens") or 0)),
            duration_ms=self._run_duration_ms(record),
        )
        for event in self._iter_session_events(session_id):
            if not _event_belongs_to_run(
                event=event,
                run_id=run_id,
                request_id=request_id,
            ):
                continue
            payload = event.get("payload")
            payload_map = dict(payload) if isinstance(payload, dict) else {}
            event_type = str(event.get("event_type", "") or "").strip()
            if event_type == "llm.call.completed":
                usage_delta = _usage_stats_from_payload(payload_map)
                stats = stats.add(
                    RunStats(
                        cache_read_tokens=usage_delta.cache_read_tokens,
                        llm_calls=1,
                    )
                )
            elif _is_tool_request_event(event_type):
                stats = stats.add(RunStats(tool_calls=1))
            elif _is_tool_result_event(event_type):
                stats = stats.add(
                    RunStats(tool_errors=_tool_error_delta(event_type, payload_map))
                )
        return RunStatsSummary(
            session_id=session_id,
            run_id=str(run_id),
            stats=stats,
        )

    def get_session_stats(self, session_id: str) -> SessionStatsSummary:
        aggregate = RunStats()
        top_tools: Counter[str] = Counter()
        turn_count = 0
        for event in self._iter_session_events(session_id):
            payload = event.get("payload")
            payload_map = dict(payload) if isinstance(payload, dict) else {}
            event_type = str(event.get("event_type", "") or "").strip()
            if event_type == "turn.assistant":
                turn_count += 1
                continue
            if event_type == "llm.call.completed":
                aggregate = aggregate.add(_usage_stats_from_payload(payload_map))
                continue
            if _is_tool_request_event(event_type):
                aggregate = aggregate.add(RunStats(tool_calls=1))
                tool_name = _tool_name_from_payload(payload_map)
                if tool_name:
                    top_tools[tool_name] += 1
                continue
            if _is_tool_result_event(event_type):
                aggregate = aggregate.add(
                    RunStats(tool_errors=_tool_error_delta(event_type, payload_map))
                )
        if (turn_count == 0 or not aggregate.has_any_data) and hasattr(
            self._store, "list_messages"
        ):
            message_turn_count, message_aggregate = self._message_backfill(session_id)
            if turn_count == 0:
                turn_count = message_turn_count
            if not aggregate.has_any_data:
                aggregate = aggregate.add(message_aggregate)
        aggregate = aggregate.add(
            RunStats(
                duration_ms=sum(
                    self._run_duration_ms(record)
                    for record in self._list_run_records(session_id)
                )
            )
        )
        return SessionStatsSummary(
            session_id=session_id,
            turn_count=max(0, int(turn_count)),
            stats=aggregate,
            top_tools=tuple(
                ToolCallCount(name=name, calls=count)
                for name, count in top_tools.most_common(5)
            ),
        )

    def _iter_session_events(self, session_id: str) -> list[dict[str, Any]]:
        return list(self._read_session_events(session_id).events)

    def _read_session_events(
        self,
        session_id: str,
        *,
        event_limit: int | None = None,
    ) -> _EventReadResult:
        normalized_limit = _normalize_event_limit(event_limit)
        if hasattr(self._store, "get_events"):
            fetch_limit = normalized_limit + 1 if normalized_limit is not None else None
            events = self._store.get_events(session_id, limit=fetch_limit)
            applied_limit = normalized_limit
        elif hasattr(self._store, "list_events"):
            applied_limit = normalized_limit or RUNTIME_EVENT_READ_LIMIT
            events = self._store.list_events(
                session_id=session_id,
                limit=applied_limit + 1,
                newest_first=False,
            )
        else:
            return _EventReadResult(
                events=(),
                complete=False,
                events_scanned=0,
                event_limit=normalized_limit,
            )
        raw_events = list(events)
        complete = applied_limit is None or len(raw_events) <= applied_limit
        included_events = (
            raw_events[:applied_limit] if applied_limit is not None else raw_events
        )
        return _EventReadResult(
            events=tuple(_normalize_session_event(event) for event in included_events),
            complete=complete,
            events_scanned=len(raw_events),
            event_limit=applied_limit,
        )

    @staticmethod
    def _build_token_usage_summary(
        *,
        session_id: str,
        usage_events: list[dict[str, Any]],
        read: _EventReadResult,
        run_id: str = "",
    ) -> TokenUsageSummary:
        ordered_events = sort_session_events(usage_events)
        records: list[TokenUsageRecord] = []
        for event in ordered_events:
            event_records = records_from_session_event(event, session_id=session_id)
            if run_id:
                event_records = tuple(
                    replace(record, run_id=record.run_id or run_id)
                    for record in event_records
                )
            records.extend(event_records)
        first_event = (
            event_ref_from_session_event(ordered_events[0]) if ordered_events else None
        )
        last_event = (
            event_ref_from_session_event(ordered_events[-1]) if ordered_events else None
        )
        return TokenUsageSummary(
            session_id=session_id,
            run_id=run_id,
            records=tuple(records),
            complete=read.complete,
            source_event_count=len(ordered_events),
            events_scanned=read.events_scanned,
            event_limit=read.event_limit,
            first_source_event=first_event,
            last_source_event=last_event,
        )

    def _list_run_records(self, session_id: str) -> list[dict[str, Any]]:
        if not hasattr(self._store, "list_run_records"):
            return []
        return list(self._store.list_run_records(session_id))

    def _message_backfill(self, session_id: str) -> tuple[int, RunStats]:
        messages = self._store.list_messages(session_id=session_id, limit=10000)
        turn_count = 0
        aggregate = RunStats()
        for message in messages:
            role = str(getattr(message, "role", "") or "").strip().lower()
            if role not in {"assistant", "outbound"}:
                continue
            turn_count += 1
            metadata = getattr(message, "metadata", {}) or {}
            if not isinstance(metadata, dict):
                continue
            raw_stats = metadata.get("run_stats_json")
            if isinstance(raw_stats, str):
                try:
                    raw_stats = json.loads(raw_stats)
                except ValueError:
                    raw_stats = None
            stats = RunStats.from_mapping(
                raw_stats if isinstance(raw_stats, dict) else None
            )
            if stats is not None:
                aggregate = aggregate.add(stats)
        return turn_count, aggregate

    @staticmethod
    def _run_duration_ms(record: dict[str, Any]) -> int:
        started_at = _normalize_iso_datetime(record.get("started_at"))
        finished_at = _normalize_iso_datetime(record.get("finished_at"))
        if started_at is None or finished_at is None:
            return 0
        delta_ms = int((finished_at - started_at).total_seconds() * 1000)
        return max(0, delta_ms)
