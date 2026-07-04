from collections import Counter
from datetime import datetime
import json
from typing import Any

from openminion.modules.storage.runtime.session_store import SessionStore
from openminion.services.stats.token_usage import (
    TokenUsageSummary,
    records_from_session_event,
)
from openminion.services.stats.types import (
    RunStats,
    RunStatsSummary,
    SessionStatsSummary,
    ToolCallCount,
)


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


def _is_tool_request_event(event_type: str) -> bool:
    return event_type in {"tool.request", "tool.requested", "tool.started"}


def _is_tool_result_event(event_type: str) -> bool:
    return event_type in {
        "tool.completed",
        "tool.failed",
        "tool.call.blocked",
    }


class StatsService:
    def __init__(self, store: SessionStore) -> None:
        self._store = store

    def get_run_token_usage(self, run_id: str) -> TokenUsageSummary | None:
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
        records = []
        for event in self._iter_session_events(session_id):
            if not _event_belongs_to_run(
                event=event,
                run_id=run_id,
                request_id=request_id,
            ):
                continue
            records.extend(records_from_session_event(event, session_id=session_id))
        return TokenUsageSummary(
            session_id=session_id,
            run_id=str(run_id),
            records=tuple(records),
        )

    def get_session_token_usage(self, session_id: str) -> TokenUsageSummary:
        records = []
        for event in self._iter_session_events(session_id):
            records.extend(records_from_session_event(event, session_id=session_id))
        return TokenUsageSummary(session_id=session_id, records=tuple(records))

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
        if hasattr(self._store, "get_events"):
            events = self._store.get_events(session_id)
        elif hasattr(self._store, "list_events"):
            events = self._store.list_events(
                session_id=session_id,
                limit=10000,
                newest_first=False,
            )
        else:
            return []
        normalized: list[dict[str, Any]] = []
        for event in events:
            if isinstance(event, dict):
                normalized.append(dict(event))
                continue
            normalized.append(
                {
                    "event_id": str(getattr(event, "event_id", "") or ""),
                    "event_type": str(getattr(event, "event_type", "") or ""),
                    "payload": getattr(event, "payload", {}) or {},
                    "trace_id": str(getattr(event, "trace_id", "") or ""),
                }
            )
        return normalized

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
