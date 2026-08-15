from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from openminion.modules.telemetry.cli import main
from openminion.base.config import OTELExporterConfig
from openminion.modules.telemetry.inspection import (
    build_telemetry_debug_report,
    select_recent_invocation_ids,
)
from openminion.modules.telemetry.invocation_inspection import (
    build_invocation_snapshot,
)
from openminion.modules.telemetry.schemas import TelemetryEvent
from openminion.modules.telemetry.service import TelemetryCtl, TelemetryService


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_telemetryctl_summary_prints_sorted_module_and_metric_keys(
    capsys, tmp_path: Path
) -> None:
    db_path = tmp_path / ".openminion" / "telemetry.db"
    service = TelemetryService(str(db_path))
    ctl = TelemetryCtl(service)
    _run(
        ctl.emit_module_operation(
            "sess-cli",
            "turn-1",
            "openminion-tool",
            "completed",
            extra={"tool": "echo"},
        )
    )
    _run(
        ctl.emit_module_counter(
            "sess-cli",
            "turn-1",
            "openminion-tool",
            "latency_bucket_ms",
            20.0,
        )
    )
    _run(
        ctl.emit_module_operation(
            "sess-cli",
            "turn-1",
            "openminion-brain",
            "llm_pack",
        )
    )
    _run(service.close())

    assert main(["summary", "--db", str(db_path), "sess-cli"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert list(payload.keys()) == ["openminion-brain", "openminion-tool"]
    assert list(payload["openminion-tool"]["custom_counter_sums"].keys()) == [
        "latency_bucket_ms"
    ]
    assert list(payload["openminion-tool"]["operation_counts"].keys()) == ["completed"]


def test_invocation_summary_reports_legacy_orphan_and_propagation_diagnostics(
    tmp_path: Path,
) -> None:
    service = TelemetryService(str(tmp_path / "telemetry.db"))
    try:
        service.record_event_sync(
            TelemetryEvent(
                session_id="session-1",
                turn_id="turn-1",
                invocation_id="invocation-1",
                event_type="agent.execution.failed",
                data={"trace_context_status": "invalid", "status": "error"},
            )
        )
        payload, _events = build_invocation_snapshot(service, "invocation-1")
    finally:
        service.close_sync()

    assert payload["diagnostics"]["orphan_terminal_events"] == 1
    assert payload["diagnostics"]["propagation"]["invalid"] == 1


def test_debug_report_selects_latest_and_aggregates_direct_facts(
    tmp_path: Path,
) -> None:
    trace_root = tmp_path / "traces"
    trace_path = trace_root / "llm/agent/run/step01-call01.json"
    trace_path.parent.mkdir(parents=True)
    trace_path.write_text("{}", encoding="utf-8")
    service = TelemetryService(str(tmp_path / "telemetry.db"))
    try:
        for invocation_id in ("invocation-a", "invocation-b"):
            service.record_event_sync(
                TelemetryEvent(
                    session_id="session-1",
                    turn_id="turn-1",
                    event_type="agent.invocation.started",
                    event_id=f"start-{invocation_id}",
                    timestamp=10.5,
                    invocation_id=invocation_id,
                    execution_id=f"execution-{invocation_id}",
                    agent_id="agent-1",
                )
            )
        service.record_event_sync(
            TelemetryEvent(
                session_id="session-1",
                turn_id="turn-1",
                event_type="llm.call.completed",
                event_id="call-1",
                timestamp=11.0,
                invocation_id="invocation-b",
                data={
                    "llm_call_id": "call-1",
                    "usage": {
                        "prompt_tokens": 2,
                        "completion_tokens": 3,
                        "total_tokens": 5,
                    },
                    "cost_usd": "0.0100",
                    "cost_source": "provider",
                    "trace_artifact_paths": [str(trace_path.relative_to(trace_root))],
                    "trace_artifacts_complete": True,
                },
            )
        )
        service.record_event_sync(
            TelemetryEvent(
                session_id="session-1",
                turn_id="turn-1",
                event_type="agent.invocation.completed",
                event_id="terminal-b",
                timestamp=12.0,
                invocation_id="invocation-b",
                data={"provider": "provider-1", "model": "model-1"},
            )
        )

        payload = build_telemetry_debug_report(
            service,
            trace_root=trace_root,
            exporter_config=OTELExporterConfig(),
        ).to_dict()
    finally:
        service.close_sync()

    assert payload["schema_version"] == "openminion.telemetry_debug.v1"
    assert payload["selection"]["selected_invocation_id"] == "invocation-b"
    assert payload["invocation"]["outcome"] == "completed"
    assert payload["invocation"]["provider"] == "provider-1"
    assert payload["invocation"]["model"] == "model-1"
    assert payload["invocation"]["duration_ms"] == 1500
    assert payload["invocation"]["usage"] == {
        "input_tokens": 2,
        "output_tokens": 3,
        "total_tokens": 5,
        "cost_usd": "0.01",
        "llm_call_count": 1,
        "calls_with_usage": 1,
        "complete": True,
    }
    assert payload["invocation"]["trace_count"] == 1
    assert payload["links"]["commands"] == [
        "telemetryctl debug bundle invocation-b",
        "telemetryctl invocation graph invocation-b",
        "telemetryctl invocation show invocation-b",
    ]
    assert payload["links"]["trace_paths"] == ["llm/agent/run/step01-call01.json"]


def test_debug_report_failed_selector_uses_canonical_terminal(tmp_path: Path) -> None:
    service = TelemetryService(str(tmp_path / "telemetry.db"))
    try:
        for invocation_id, terminal_type, timestamp in (
            ("failed-old", "agent.invocation.failed", 2.0),
            ("recovered", "agent.invocation.failed", 3.0),
            ("recovered", "agent.invocation.completed", 4.0),
        ):
            service.record_event_sync(
                TelemetryEvent(
                    session_id="session-1",
                    turn_id="turn-1",
                    event_type=terminal_type,
                    event_id=f"{invocation_id}-{timestamp}",
                    timestamp=timestamp,
                    invocation_id=invocation_id,
                )
            )
        payload = build_telemetry_debug_report(
            service,
            selector_kind="failed",
            exporter_config=OTELExporterConfig(),
        ).to_dict()
    finally:
        service.close_sync()

    assert payload["selection"]["selected_invocation_id"] == "failed-old"
    assert payload["invocation"]["outcome"] == "failed"
    assert any(
        item["code"] == "CONFLICTING_TERMINALS" for item in payload["diagnostics"]
    )


def test_debug_report_opaque_lookup_and_mid_page_failure(tmp_path: Path) -> None:
    service = TelemetryService(str(tmp_path / "telemetry.db"))
    try:
        for index in range(1001):
            service.record_event_sync(
                TelemetryEvent(
                    session_id="session-1",
                    turn_id="turn-1",
                    event_type="tick",
                    event_id=f"event-{index}",
                    timestamp=float(index),
                    invocation_id="invocation-1",
                )
            )
        original = service._store.fetch_event_page
        calls = 0

        def fail_second_detail_page(**kwargs):
            nonlocal calls
            page = original(**kwargs)
            if kwargs.get("invocation_id") == "invocation-1":
                calls += 1
                if calls == 2:
                    raise OSError("read failed")
            return page

        service._store.fetch_event_page = fail_second_detail_page
        payload = build_telemetry_debug_report(
            service,
            selector_kind="invocation_id",
            invocation_id="invocation-1",
            exporter_config=OTELExporterConfig(),
        ).to_dict()
    finally:
        service.close_sync()

    assert payload["status"] == "error"
    assert payload["error"] == {
        "code": "TELEMETRY_STORAGE_FAILURE",
        "category": "storage",
    }


def test_debug_report_exhausts_more_than_one_page_at_captured_high_water(
    tmp_path: Path,
) -> None:
    service = TelemetryService(str(tmp_path / "telemetry.db"))
    try:
        service.record_event_sync(
            TelemetryEvent(
                session_id="session-1",
                turn_id="turn-1",
                event_type="agent.invocation.started",
                event_id="start",
                timestamp=1.0,
                invocation_id="invocation-1",
            )
        )
        for index in range(1001):
            service.record_event_sync(
                TelemetryEvent(
                    session_id="session-1",
                    turn_id="turn-1",
                    event_type="llm.call.completed",
                    event_id=f"call-{index}",
                    timestamp=float(index + 2),
                    invocation_id="invocation-1",
                    data={
                        "llm_call_id": f"call-{index}",
                        "usage": {
                            "input_tokens": 1,
                            "output_tokens": 1,
                            "total_tokens": 2,
                        },
                        "trace_artifact_paths": [],
                        "trace_artifacts_complete": True,
                    },
                )
            )
        original = service._store.fetch_event_page
        inserted = False

        def insert_after_first_detail_page(**kwargs):
            nonlocal inserted
            page = original(**kwargs)
            if kwargs.get("invocation_id") == "invocation-1" and not inserted:
                inserted = True
                service.record_event_sync(
                    TelemetryEvent(
                        session_id="session-1",
                        turn_id="turn-1",
                        event_type="llm.call.completed",
                        event_id="late-call",
                        timestamp=5000.0,
                        invocation_id="invocation-1",
                        data={
                            "llm_call_id": "late-call",
                            "usage": {
                                "input_tokens": 99,
                                "output_tokens": 99,
                                "total_tokens": 198,
                            },
                            "trace_artifact_paths": [],
                            "trace_artifacts_complete": True,
                        },
                    )
                )
            return page

        service._store.fetch_event_page = insert_after_first_detail_page
        payload = build_telemetry_debug_report(
            service,
            exporter_config=OTELExporterConfig(),
        ).to_dict()
    finally:
        service.close_sync()

    usage = payload["invocation"]["usage"]
    assert usage["llm_call_count"] == 1001
    assert usage["calls_with_usage"] == 1001
    assert usage["input_tokens"] == 1001
    assert usage["output_tokens"] == 1001
    assert usage["total_tokens"] == 2002
    assert payload["invocation"]["trace_count"] == 0


def test_debug_report_usage_conflicts_invalid_values_and_overflow(
    tmp_path: Path,
) -> None:
    service = TelemetryService(str(tmp_path / "telemetry.db"))
    try:
        service.record_event_sync(
            TelemetryEvent(
                session_id="session-1",
                turn_id="turn-1",
                event_type="agent.invocation.started",
                event_id="start",
                timestamp=1.0,
                invocation_id="invocation-1",
            )
        )
        for event_id, call_id, usage in (
            (
                "call-1",
                "call-1",
                {
                    "input_tokens": 1,
                    "prompt_tokens": 2,
                    "output_tokens": 3,
                    "total_tokens": 4,
                },
            ),
            ("call-2", "call-2", {"input_tokens": -1}),
            (
                "call-3",
                "call-3",
                {
                    "input_tokens": 2**63 - 1,
                    "output_tokens": 0,
                    "total_tokens": 2**63 - 1,
                },
            ),
        ):
            service.record_event_sync(
                TelemetryEvent(
                    session_id="session-1",
                    turn_id="turn-1",
                    event_type="llm.call.completed",
                    event_id=event_id,
                    timestamp=2.0,
                    invocation_id="invocation-1",
                    data={
                        "llm_call_id": call_id,
                        "usage": usage,
                        "trace_artifact_paths": [],
                        "trace_artifacts_complete": True,
                    },
                )
            )
        payload = build_telemetry_debug_report(
            service,
            exporter_config=OTELExporterConfig(),
        ).to_dict()
    finally:
        service.close_sync()

    usage = payload["invocation"]["usage"]
    assert usage["llm_call_count"] == 3
    assert usage["calls_with_usage"] == 2
    assert usage["input_tokens"] is None
    assert usage["output_tokens"] == 3
    assert usage["total_tokens"] is None
    assert usage["complete"] is False
    codes = {item["code"] for item in payload["diagnostics"]}
    assert {
        "CONFLICTING_USAGE_ALIASES",
        "INVALID_USAGE_FACT",
        "PARTIAL_USAGE",
    }.issubset(codes)


def test_recent_invocation_selector_defaults_to_twenty_and_validates_first(
    tmp_path: Path,
) -> None:
    service = TelemetryService(str(tmp_path / "telemetry.db"))
    try:
        for index in range(25):
            service.record_event_sync(
                TelemetryEvent(
                    session_id="session-1",
                    turn_id="turn-1",
                    event_type="agent.invocation.started",
                    event_id=f"start-{index}",
                    timestamp=float(index),
                    invocation_id=f"invocation-{index:02d}",
                )
            )
        assert select_recent_invocation_ids(service) == [
            f"invocation-{index:02d}" for index in range(24, 4, -1)
        ]
        service._store.event_high_water = lambda **_kwargs: pytest.fail(
            "storage must not be called"
        )
        with pytest.raises(ValueError, match="between 1 and 1000"):
            select_recent_invocation_ids(service, limit=1001)
    finally:
        service.close_sync()
