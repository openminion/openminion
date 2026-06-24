import asyncio
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
from typing import Any, Mapping, Optional

from openminion.base.config import OTELExporterConfig
from openminion.base.config.paths import ensure_under_data_root
from openminion.modules.config import (
    is_module_standalone_mode,
    resolve_module_data_root,
    resolve_module_home_root,
)
from .export.otel import OpenTelemetryTraceExporter
from .constants import (
    DEFAULT_INTEGRATED_SQLITE_SUBPATH,
    DEFAULT_STANDALONE_SQLITE_SUBPATH,
)
from openminion.modules.storage.record_store import RecordStore
from .storage.store import PostgresTelemetryStore, SQLiteTelemetryStore

from .interfaces import TELEMETRY_INTERFACE_VERSION
from .schemas import (
    TelemetryEvent,
    SessionTelemetry,
    ModuleTelemetryStats,
    CostSummary,
    calculate_cost,
)

_LOG = logging.getLogger(__name__)


class TelemetryService:
    """Service for recording and retrieving telemetry events."""

    def __init__(
        self,
        db_path: Optional[str] = None,
        *,
        home_root: Optional[str | Path] = None,
        env: Optional[Mapping[str, str]] = None,
        record_store: RecordStore | None = None,
        otel_exporter_config: OTELExporterConfig | None = None,
    ) -> None:
        path_info = resolve_telemetry_db_path(
            db_path=db_path,
            home_root=home_root,
            env=env,
        )
        Path(path_info.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db_path = path_info.db_path
        self._path_mode = path_info.path_mode
        self._path_source = path_info.path_source
        self._home_root = path_info.home_root
        if record_store is not None:
            self._store = PostgresTelemetryStore(record_store=record_store)
        else:
            self._store = SQLiteTelemetryStore(self._db_path)
        self._otel_exporter = OpenTelemetryTraceExporter(
            otel_exporter_config,
            logger=_LOG,
        )

    @property
    def contract_version(self) -> str:
        """Interface contract version for this implementation."""
        return TELEMETRY_INTERFACE_VERSION

    async def close(self) -> None:
        self._otel_exporter.close()
        await asyncio.to_thread(self._store.close)

    def close_sync(self) -> None:
        self._otel_exporter.close()
        self._store.close()

    async def record_event(self, event: TelemetryEvent) -> None:
        """Record a telemetry event."""
        payload = dict(event.data or {})
        if event.mode and "mode" not in payload:
            payload["mode"] = str(event.mode).strip().lower()
        await asyncio.to_thread(
            self._store.insert_event,
            session_id=event.session_id,
            turn_id=event.turn_id,
            event_type=event.event_type,
            timestamp=event.timestamp,
            data=payload,
        )
        self._otel_exporter.export(
            TelemetryEvent(
                session_id=event.session_id,
                turn_id=event.turn_id,
                event_type=event.event_type,
                timestamp=event.timestamp,
                data=payload,
                mode=event.mode,
            )
        )

    def record_event_sync(self, event: TelemetryEvent) -> None:
        """Record a telemetry event from sync runtime hooks."""
        payload = dict(event.data or {})
        if event.mode and "mode" not in payload:
            payload["mode"] = str(event.mode).strip().lower()
        self._store.insert_event(
            session_id=event.session_id,
            turn_id=event.turn_id,
            event_type=event.event_type,
            timestamp=event.timestamp,
            data=payload,
        )
        self._otel_exporter.export(
            TelemetryEvent(
                session_id=event.session_id,
                turn_id=event.turn_id,
                event_type=event.event_type,
                timestamp=event.timestamp,
                data=payload,
                mode=event.mode,
            )
        )

    async def record_metric(
        self, name: str, value: float, tags: Optional[dict[str, str]] = None
    ) -> None:
        """Record a metric event."""
        event = TelemetryEvent(
            session_id="metric",
            turn_id="metric",
            event_type="metric",
            data={"name": name, "value": value, "tags": tags or {}},
        )
        await self.record_event(event)

    async def get_session_summary(self, session_id: str) -> SessionTelemetry:
        """Get aggregated telemetry for a session."""
        rows = await asyncio.to_thread(self._store.fetch_session_events, session_id)

        tick_count = 0
        tool_call_count = 0
        llm_call_count = 0
        input_tokens = 0
        output_tokens = 0
        cached_tokens = 0
        first_ts: Optional[float] = None
        last_ts: Optional[float] = None
        events: list[TelemetryEvent] = []
        module_stats: dict[str, ModuleTelemetryStats] = {}

        for turn_id, event_type, timestamp, data_str in rows:
            data = json.loads(data_str)
            events.append(
                TelemetryEvent(
                    session_id=session_id,
                    turn_id=turn_id,
                    event_type=event_type,
                    timestamp=float(timestamp),
                    data=data,
                    mode=str(data.get("mode", "")).strip().lower() or None,
                )
            )

            if first_ts is None:
                first_ts = float(timestamp)
            last_ts = float(timestamp)

            if event_type == "tick":
                tick_count += 1
            elif event_type == "tool_call":
                tool_call_count += 1
            elif event_type == "llm_call":
                llm_call_count += 1
                input_tokens += data.get("input_tokens", 0)
                output_tokens += data.get("output_tokens", 0)
                cached_tokens += data.get("cached_tokens", 0)

            module_id = self._infer_module_id(event_type, data)
            if module_id:
                stats = module_stats.get(module_id)
                if stats is None:
                    stats = ModuleTelemetryStats(module_id=module_id)
                    module_stats[module_id] = stats

                stats.event_count += 1
                stats.last_turn_id = str(turn_id)
                stats.total_latency_ms += float(
                    data.get("latency_ms", data.get("elapsed_ms", 0.0)) or 0.0
                )
                stats.total_input_tokens += int(data.get("input_tokens", 0) or 0)
                stats.total_output_tokens += int(data.get("output_tokens", 0) or 0)
                stats.total_cached_tokens += int(data.get("cached_tokens", 0) or 0)
                stats.total_dropped_items += int(data.get("dropped_items", 0) or 0)
                stats.total_truncated_items += int(data.get("truncated_items", 0) or 0)
                self._aggregate_module_operations(stats, data)
                self._aggregate_module_counters(stats, data)

                success = self._is_success_status(data)
                if success:
                    stats.success_count += 1
                else:
                    stats.error_count += 1

        elapsed_ms = (
            (last_ts - first_ts) * 1000
            if first_ts is not None and last_ts is not None
            else 0
        )

        return SessionTelemetry(
            session_id=session_id,
            event_count=len(events),
            tick_count=tick_count,
            tool_call_count=tool_call_count,
            llm_call_count=llm_call_count,
            total_input_tokens=input_tokens,
            total_output_tokens=output_tokens,
            total_cached_tokens=cached_tokens,
            elapsed_ms=elapsed_ms,
            module_stats=module_stats,
            events=events,
        )

    async def get_module_summary(self, session_id: str) -> dict[str, dict[str, Any]]:
        """Get module-level aggregated telemetry stats for a session."""
        summary = await self.get_session_summary(session_id)
        return {mid: stats.to_dict() for mid, stats in summary.module_stats.items()}

    async def get_session_cost(
        self,
        session_id: str,
        provider: str = "default",
        model: str = "default",
    ) -> CostSummary:
        """Get cost summary for a session."""
        summary = await self.get_session_summary(session_id)
        cost = calculate_cost(
            summary.total_input_tokens,
            summary.total_output_tokens,
            summary.total_cached_tokens,
            provider,
            model,
        )
        return CostSummary(
            session_id=session_id,
            input_tokens=summary.total_input_tokens,
            output_tokens=summary.total_output_tokens,
            cached_tokens=summary.total_cached_tokens,
            estimated_cost_usd=cost,
            provider=provider,
            model=model,
        )

    async def get_session_cost_by_mode(
        self,
        session_id: str,
        provider: str = "default",
        model: str = "default",
    ) -> dict[str, CostSummary]:
        summary = await self.get_session_summary(session_id)
        by_mode: dict[str, dict[str, int]] = {}
        for event in summary.events:
            if event.event_type != "llm_call":
                continue
            mode = (
                str(event.mode or event.data.get("mode") or "").strip().lower()
                or "unknown"
            )
            bucket = by_mode.setdefault(
                mode,
                {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0},
            )
            bucket["input_tokens"] += int(event.data.get("input_tokens", 0) or 0)
            bucket["output_tokens"] += int(event.data.get("output_tokens", 0) or 0)
            bucket["cached_tokens"] += int(event.data.get("cached_tokens", 0) or 0)

        result: dict[str, CostSummary] = {}
        for mode_name, totals in by_mode.items():
            result[mode_name] = CostSummary(
                session_id=session_id,
                input_tokens=totals["input_tokens"],
                output_tokens=totals["output_tokens"],
                cached_tokens=totals["cached_tokens"],
                estimated_cost_usd=calculate_cost(
                    totals["input_tokens"],
                    totals["output_tokens"],
                    totals["cached_tokens"],
                    provider,
                    model,
                ),
                provider=provider,
                model=model,
            )
        return result

    def get_path_debug(self) -> dict[str, Any]:
        return {
            "db_path": self._db_path,
            "path_mode": self._path_mode,
            "path_source": self._path_source,
            "home_root": self._home_root,
        }

    @staticmethod
    def _infer_module_id(event_type: str, data: dict[str, Any]) -> str:
        module_id = str(data.get("module_id", "")).strip()
        if module_id:
            return module_id

        default_map = {
            "tick": "openminion-runtime",
            "tool_call": "openminion-tool",
            "llm_call": "openminion-llm",
            "context_pack": "openminion-context",
            "metric": "openminion-telemetry",
        }
        return default_map.get(event_type, "")

    @staticmethod
    def _is_success_status(data: dict[str, Any]) -> bool:
        if "success" in data:
            return bool(data.get("success"))

        status = str(data.get("status", "")).strip().lower()
        if status in {"error", "failed", "fail", "blocked"}:
            return False
        if status in {"ok", "success", "succeeded", "completed"}:
            return True

        return True

    @staticmethod
    def _aggregate_module_operations(
        stats: ModuleTelemetryStats,
        data: dict[str, Any],
    ) -> None:
        operation = str(data.get("operation", "")).strip()
        if not operation:
            return

        raw_count = data.get("operation_count", data.get("count", 1))
        try:
            count = int(raw_count)
        except (TypeError, ValueError):
            count = 1
        if count < 0:
            count = 0

        stats.operation_counts[operation] = (
            stats.operation_counts.get(operation, 0) + count
        )
        stats.last_operation = operation

    @staticmethod
    def _aggregate_module_counters(
        stats: ModuleTelemetryStats,
        data: dict[str, Any],
    ) -> None:
        counter_name = str(data.get("counter_name", "")).strip()
        if counter_name:
            raw_value = data.get("counter_value", 0)
            try:
                value = float(raw_value)
            except (TypeError, ValueError):
                value = 0.0
            stats.custom_counter_sums[counter_name] = (
                stats.custom_counter_sums.get(counter_name, 0.0) + value
            )

        counters = data.get("counters")
        if isinstance(counters, dict):
            for key, raw_value in counters.items():
                if not isinstance(key, str) or not key.strip():
                    continue
                try:
                    value = float(raw_value)
                except (TypeError, ValueError):
                    continue
                clean_key = key.strip()
                stats.custom_counter_sums[clean_key] = (
                    stats.custom_counter_sums.get(clean_key, 0.0) + value
                )


class TelemetryCtl:
    """Control interface for telemetry adapter wiring."""

    def __init__(self, service: TelemetryService) -> None:
        self._service = service

    async def emit_tick(
        self,
        session_id: str,
        turn_id: str,
        elapsed_ms: float,
        mode: str | None = None,
    ) -> None:
        """Emit a tick event."""
        await self._service.record_event(
            TelemetryEvent(
                session_id=session_id,
                turn_id=turn_id,
                event_type="tick",
                mode=mode,
                data={
                    "module_id": "openminion-runtime",
                    "status": "ok",
                    "elapsed_ms": elapsed_ms,
                    "latency_ms": elapsed_ms,
                },
            )
        )

    async def emit_tool_call(
        self,
        session_id: str,
        turn_id: str,
        tool_name: str,
        success: bool,
        mode: str | None = None,
    ) -> None:
        """Emit a tool call event."""
        await self._service.record_event(
            TelemetryEvent(
                session_id=session_id,
                turn_id=turn_id,
                event_type="tool_call",
                mode=mode,
                data={
                    "module_id": "openminion-tool",
                    "status": "ok" if success else "error",
                    "tool_name": tool_name,
                    "success": success,
                },
            )
        )

    async def emit_llm_call(
        self,
        session_id: str,
        turn_id: str,
        input_tokens: int,
        output_tokens: int,
        cached_tokens: int = 0,
        mode: str | None = None,
    ) -> None:
        """Emit an LLM call event with token usage."""
        await self._service.record_event(
            TelemetryEvent(
                session_id=session_id,
                turn_id=turn_id,
                event_type="llm_call",
                mode=mode,
                data={
                    "module_id": "openminion-llm",
                    "status": "ok",
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cached_tokens": cached_tokens,
                },
            )
        )

    async def emit_context_pack(
        self,
        session_id: str,
        turn_id: str,
        tokens: int,
        mode: str | None = None,
    ) -> None:
        """Emit a context pack event."""
        await self._service.record_event(
            TelemetryEvent(
                session_id=session_id,
                turn_id=turn_id,
                event_type="context_pack",
                mode=mode,
                data={
                    "module_id": "openminion-context",
                    "status": "ok",
                    "tokens": tokens,
                },
            )
        )

    async def emit_module_stats(
        self,
        session_id: str,
        turn_id: str,
        module_id: str,
        *,
        status: str = "ok",
        latency_ms: float = 0.0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cached_tokens: int = 0,
        dropped_items: int = 0,
        truncated_items: int = 0,
        extra: Optional[dict[str, Any]] = None,
        mode: str | None = None,
    ) -> None:
        """Emit module-level stats for one module invocation within a turn."""
        payload: dict[str, Any] = {
            "module_id": module_id,
            "status": status,
            "latency_ms": latency_ms,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cached_tokens": cached_tokens,
            "dropped_items": dropped_items,
            "truncated_items": truncated_items,
        }
        if extra:
            payload.update(extra)

        await self._service.record_event(
            TelemetryEvent(
                session_id=session_id,
                turn_id=turn_id,
                event_type="module.stats",
                mode=mode,
                data=payload,
            )
        )

    async def emit_module_operation(
        self,
        session_id: str,
        turn_id: str,
        module_id: str,
        operation: str,
        *,
        count: int = 1,
        status: str = "ok",
        latency_ms: float = 0.0,
        extra: Optional[dict[str, Any]] = None,
        mode: str | None = None,
    ) -> None:
        """Emit a module operation count (for example run/stop/kill)."""
        op = str(operation or "").strip()
        if not op:
            raise ValueError("operation must be non-empty")
        delta = int(count)
        if delta < 0:
            raise ValueError("operation_count must be non-negative")

        payload_extra: dict[str, Any] = {
            "operation": op,
            "operation_count": delta,
        }
        if extra:
            payload_extra.update(extra)

        await self.emit_module_stats(
            session_id=session_id,
            turn_id=turn_id,
            module_id=module_id,
            status=status,
            latency_ms=latency_ms,
            extra=payload_extra,
            mode=mode,
        )

    async def emit_module_counter(
        self,
        session_id: str,
        turn_id: str,
        module_id: str,
        counter_name: str,
        value: float,
        *,
        status: str = "ok",
        extra: Optional[dict[str, Any]] = None,
        mode: str | None = None,
    ) -> None:
        """Emit a generic module counter/sum metric."""
        name = str(counter_name or "").strip()
        if not name:
            raise ValueError("counter_name must be non-empty")
        numeric_value = float(value)
        if numeric_value < 0:
            raise ValueError("counter_value must be non-negative")

        payload_extra: dict[str, Any] = {
            "counter_name": name,
            "counter_value": numeric_value,
        }
        if extra:
            payload_extra.update(extra)

        await self.emit_module_stats(
            session_id=session_id,
            turn_id=turn_id,
            module_id=module_id,
            status=status,
            extra=payload_extra,
            mode=mode,
        )

    async def emit_tool_exec_operation(
        self,
        session_id: str,
        turn_id: str,
        operation: str,
        *,
        count: int = 1,
        success: bool = True,
        latency_ms: float = 0.0,
        extra: Optional[dict[str, Any]] = None,
        mode: str | None = None,
    ) -> None:
        """Convenience emitter for tool exec operations."""
        await self.emit_module_operation(
            session_id=session_id,
            turn_id=turn_id,
            module_id="openminion-tool",
            operation=operation,
            count=count,
            status="ok" if success else "error",
            latency_ms=latency_ms,
            extra=extra,
            mode=mode,
        )

    async def emit_canonical_event(
        self,
        session_id: str,
        turn_id: str,
        event_type: str,
        payload: Optional[dict[str, Any]] = None,
        *,
        trace_id: str | None = None,
        actor_type: str | None = None,
        status: str | None = None,
        error: Optional[dict[str, Any]] = None,
        mode: str | None = None,
    ) -> None:
        event_payload = dict(payload or {})
        if trace_id and "trace_id" not in event_payload:
            event_payload["trace_id"] = str(trace_id)
        if actor_type and "actor_type" not in event_payload:
            event_payload["actor_type"] = str(actor_type)
        if status and "status" not in event_payload:
            event_payload["status"] = str(status)
        if error and "error" not in event_payload:
            event_payload["error"] = dict(error)
        await self._service.record_event(
            TelemetryEvent(
                session_id=session_id,
                turn_id=turn_id,
                event_type=event_type,
                mode=mode,
                data=event_payload,
            )
        )

    @property
    def contract_version(self) -> str:
        """Interface contract version for this implementation."""
        return TELEMETRY_INTERFACE_VERSION


@dataclass(frozen=True)
class TelemetryPathInfo:
    db_path: str
    path_mode: str
    path_source: str
    home_root: str | None


def resolve_telemetry_db_path(
    *,
    db_path: Optional[str] = None,
    home_root: Optional[str | Path] = None,
    env: Optional[Mapping[str, str]] = None,
) -> TelemetryPathInfo:
    env_map = dict(env or os.environ)
    standalone_mode = is_module_standalone_mode(env_map)

    resolved_home_root: Path | None = None
    resolved_data_root: Path | None = None
    if not standalone_mode:
        resolved_home_root = resolve_module_home_root(
            Path(home_root) if home_root is not None else None,
            env_map,
        )
        resolved_data_root = resolve_module_data_root(
            home_root=resolved_home_root,
            env=env_map,
        )

    if db_path and str(db_path).strip():
        if str(db_path).strip() == ":memory:":
            return TelemetryPathInfo(
                db_path=":memory:",
                path_mode="module_standalone",
                path_source="explicit_override",
                home_root=str(resolved_home_root) if resolved_home_root else None,
            )
        candidate = Path(str(db_path)).expanduser()
        if not candidate.is_absolute():
            if resolved_data_root is not None:
                candidate = resolved_data_root / candidate
            elif resolved_home_root is not None:
                candidate = resolved_home_root / candidate
        resolved = candidate.resolve(strict=False)
        if resolved_data_root is not None and not standalone_mode:
            resolved = ensure_under_data_root(
                resolved, resolved_data_root, label="telemetry_db_path"
            )
        return TelemetryPathInfo(
            db_path=str(resolved),
            path_mode="module_standalone"
            if standalone_mode or resolved_home_root is None
            else "integrated_runtime",
            path_source="explicit_override",
            home_root=str(resolved_home_root) if resolved_home_root else None,
        )

    if resolved_data_root is not None and not standalone_mode:
        resolved = (resolved_data_root / DEFAULT_INTEGRATED_SQLITE_SUBPATH).resolve(
            strict=False
        )
        resolved = ensure_under_data_root(
            resolved, resolved_data_root, label="telemetry_db_path"
        )
        return TelemetryPathInfo(
            db_path=str(resolved),
            path_mode="integrated_runtime",
            path_source="default_integrated",
            home_root=str(resolved_home_root),
        )

    standalone_default = (Path.home() / DEFAULT_STANDALONE_SQLITE_SUBPATH).resolve(
        strict=False
    )
    return TelemetryPathInfo(
        db_path=str(standalone_default),
        path_mode="module_standalone",
        path_source="standalone_default",
        home_root=None,
    )


def create_telemetry_adapter(
    db_path: Optional[str] = None,
    *,
    home_root: Optional[str | Path] = None,
    env: Optional[Mapping[str, str]] = None,
    otel_exporter_config: OTELExporterConfig | None = None,
) -> TelemetryCtl:
    """Factory function to create a telemetry adapter."""
    service = TelemetryService(
        db_path,
        home_root=home_root,
        env=env,
        otel_exporter_config=otel_exporter_config,
    )
    return TelemetryCtl(service)
