from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from openminion.base.errors.contracts import ErrorInfo
from openminion.cli.status.overview import (
    HostOverview,
    OperationsOverview,
    OverviewSection,
    RuntimeOverview,
    TelemetryOverview,
    ToolActivityOverview,
    WorkItemOverview,
    WorkOverview,
    build_operations_overview,
    render_operations_overview,
)


class _TaskOwner:
    def get_digest(self, **_kwargs: object) -> object:
        return SimpleNamespace(
            tasks_active=[
                SimpleNamespace(
                    task_id="task-1",
                    title="Inspect release",
                    status="ACTIVE",
                    due_at=None,
                    next_step_id="",
                    next_step_title="",
                    metadata={},
                )
            ],
            tasks_ready=[],
            current_task=None,
        )

    def list_events(self) -> list[object]:
        return []


class _Sessions:
    def get_recent_tool_events(
        self, session_id: str, limit: int
    ) -> list[dict[str, object]]:
        assert session_id == "session-1"
        assert limit == 5
        return [
            {
                "tool_name": "file.read",
                "event_type": "tool.call.completed",
                "timestamp": "2026-08-16T12:00:00Z",
                "excerpt": "must not be rendered",
            }
        ]


def _runtime(tmp_path: Path) -> object:
    api_runtime = SimpleNamespace(task_manager=_TaskOwner(), sessions=_Sessions())
    return SimpleNamespace(
        agent_id="agent-1",
        provider_name="minimax",
        model_name="MiniMax-M2.7",
        session_id="session-1",
        transport="gateway",
        api_runtime=api_runtime,
        working_dir=str(tmp_path),
    )


def test_overview_section_requires_timestamp_for_observed_states() -> None:
    with pytest.raises(ValueError, match="stale overview sections require"):
        OverviewSection("stale", "test", None, {"value": 1})

    unavailable = OverviewSection(
        "unavailable",
        "test",
        None,
        None,
        ErrorInfo(code="NOT_AVAILABLE", message="Unavailable"),
    )
    assert unavailable.observed_at is None


def test_build_operations_overview_reuses_existing_read_owners(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    observed_at = datetime(2026, 8, 16, 12, 30, tzinfo=timezone.utc)
    report = SimpleNamespace(
        error=None,
        invocation=SimpleNamespace(
            invocation_id="invocation-1",
            outcome="success",
            duration_ms=250,
            trace_count=2,
        ),
        diagnostics=[],
    )
    monkeypatch.setattr(
        "openminion.cli.presentation.telemetry.load_telemetry_report",
        lambda *_args, **_kwargs: report,
    )
    monkeypatch.setattr(
        "openminion.tools.host.collect_host_metrics",
        lambda _workspace: (
            {
                "platform": {
                    "system": "Darwin",
                    "release": "25.5.0",
                    "machine": "arm64",
                    "python": "3.11.13",
                },
                "memory": {"used_percent": 40.0},
                "disk": [{"used_percent": 20.0, "free_bytes": 1024}],
            },
            [],
        ),
    )

    snapshot = build_operations_overview(
        _runtime(tmp_path),
        working_dir=tmp_path,
        now=observed_at,
    )

    assert snapshot.runtime.status == "available"
    assert snapshot.runtime.data is not None
    assert snapshot.runtime.data.model == "MiniMax-M2.7"
    assert snapshot.work.data == WorkOverview(
        count=1,
        statuses=(("ACTIVE", 1),),
        items=(WorkItemOverview("task-1", "Inspect release", "ACTIVE"),),
    )
    assert snapshot.recent_tools.data == (
        ToolActivityOverview(
            "file.read", "tool.call.completed", "2026-08-16T12:00:00Z"
        ),
    )
    assert snapshot.telemetry.data == TelemetryOverview(
        "invocation-1", "success", 250, 2, ()
    )
    assert snapshot.host.data == HostOverview(
        "Darwin", "25.5.0", "arm64", "3.11.13", 40.0, 20.0, 1024, ()
    )


def test_render_operations_overview_labels_sources_and_states() -> None:
    observed_at = datetime(2026, 8, 16, 12, 30, tzinfo=timezone.utc)
    unavailable = OverviewSection[tuple[ToolActivityOverview, ...]](
        "unavailable",
        "session-store",
        None,
        None,
        ErrorInfo(code="NO_STORE", message="Store unavailable"),
    )
    snapshot = OperationsOverview(
        runtime=OverviewSection(
            "available",
            "interactive-runtime",
            observed_at,
            RuntimeOverview(
                "agent-1",
                "minimax",
                "MiniMax-M2.7",
                "session-1",
                "/workspace",
                "gateway",
            ),
        ),
        work=OverviewSection(
            "stale",
            "task-surface",
            observed_at,
            WorkOverview(0, (), ()),
        ),
        recent_tools=unavailable,
        telemetry=OverviewSection(
            "available",
            "telemetry-inspection",
            observed_at,
            TelemetryOverview("", "empty", None, 0, ()),
        ),
        host=OverviewSection(
            "available",
            "host.metrics",
            observed_at,
            HostOverview("Darwin", "25.5", "arm64", "3.11", 40.0, 20.0, 1024, ()),
        ),
    )

    rendered = render_operations_overview(snapshot)

    assert "Runtime  [available]" in rendered
    assert "Active work  [stale]" in rendered
    assert "Recent tools  [unavailable]" in rendered
    assert "NO_STORE: Store unavailable" in rendered
    assert "must not be rendered" not in rendered
