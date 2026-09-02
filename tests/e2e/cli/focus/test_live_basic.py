from __future__ import annotations

import pytest

from openminion.modules.telemetry.schemas import TelemetryEvent
from openminion.modules.telemetry.service import TelemetryService
from tests.e2e.cli.focus.conftest import require_live_focus
from tests.e2e.cli.focus.harness import FocusProbe
from tests.e2e.cli.focus.harness.assertions import visible_text
from tests.e2e.cli.focus.harness.artifacts import artifact_root, write_transcript
from tests.e2e.cli.focus.harness.scenarios import BASE_LIVE_SCENARIOS

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(300)]


def _seed_foreign_invocation(focus_probe: FocusProbe) -> str:
    invocation_id = "foreign-focus-telemetry-invocation"
    service = TelemetryService(env=focus_probe.environment())
    for event_type, status in (
        ("agent.invocation.started", "running"),
        ("agent.invocation.failed", "failed"),
    ):
        service.record_event_sync(
            TelemetryEvent(
                session_id="foreign-focus-session",
                turn_id="foreign-focus-turn",
                invocation_id=invocation_id,
                event_type=event_type,
                data={"status": status, "failure_code": "FOREIGN_FAILURE"},
            )
        )
    service.close_sync()
    return invocation_id


@pytest.mark.parametrize(
    "scenario",
    BASE_LIVE_SCENARIOS,
    ids=[scenario.scenario_id for scenario in BASE_LIVE_SCENARIOS],
)
def test_live_focus_basic_turn(
    focus_probe: FocusProbe,
    scenario,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    require_live_focus()
    monkeypatch.setenv("OPENMINION_TRACE_REQUESTS", "1")
    with focus_probe.session() as session:
        focus_probe.wait_ready(session)
        focus_probe.run_turn(session, scenario)
        foreign_invocation_id = _seed_foreign_invocation(focus_probe)

        telemetry = visible_text(
            focus_probe.run_slash(
                session,
                "/telemetry",
                marker="latest invocation",
            )
        )
        assert "status: completed" in telemetry
        assert foreign_invocation_id not in telemetry
        assert "next: /telemetry failed | /telemetry invocation " in telemetry
        assert "shell: telemetryctl invocation show " in telemetry

        events = visible_text(
            focus_probe.run_slash(
                session,
                "/telemetry events --limit 20",
                marker="telemetry events:",
            )
        )
        assert "agent.invocation.completed" in events
        assert foreign_invocation_id not in events
        assert scenario.prompt not in events

        failed = visible_text(
            focus_probe.run_slash(
                session,
                "/telemetry failed",
                marker="telemetry: empty",
            )
        )
        assert foreign_invocation_id not in failed

        trace_listing = visible_text(
            focus_probe.run_slash(session, "/trace list", marker="trace files:")
        )
        assert "trace files: none" not in trace_listing
        assert "-http-response.json" in trace_listing
        assert "-structured.json" in trace_listing
        structured_traces = sorted(
            (focus_probe.data_root / "traces").rglob("*-structured.json")
        )
        assert structured_traces
        trace_path = (
            structured_traces[-1]
            .relative_to(focus_probe.data_root / "traces")
            .as_posix()
        )
        trace_summary = visible_text(
            focus_probe.run_slash(
                session,
                f"/trace show {trace_path}",
                marker="shell (raw content):",
            )
        )
        assert "trace:" in trace_summary
        assert trace_path.rsplit("/", 1)[-1] in trace_summary
        assert "shell (raw content): telemetryctl trace show" in trace_summary
        assert "--raw" in trace_summary
        assert scenario.prompt not in trace_summary

        write_transcript(
            artifact_root(tmp_path),
            scenario.scenario_id,
            session.transcript,
        )
