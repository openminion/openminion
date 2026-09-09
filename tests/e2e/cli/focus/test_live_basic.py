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


def _structured_trace_path(trace_listing: str) -> str:
    rendered = "".join(line.strip() for line in trace_listing.splitlines())
    suffix = "-structured.json"
    for item in rendered.split("llm/")[1:]:
        candidate = f"llm/{item}"
        if suffix in candidate:
            return candidate[: candidate.index(suffix) + len(suffix)]
    raise AssertionError("selected invocation has no structured trace")


def test_structured_trace_path_rejoins_terminal_wrapping() -> None:
    listing = """trace files:
  llm/invocation/step01-call01-http-response.json
  llm/invocation/step01-call01-struct
ured.json
"""

    assert _structured_trace_path(listing) == (
        "llm/invocation/step01-call01-structured.json"
    )


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
                marker="No failed model runs in this session.",
            )
        )
        assert foreign_invocation_id not in failed

        tokens = visible_text(
            focus_probe.run_slash(session, "/tokens", marker="Token usage")
        )
        assert "No model calls in this session yet." not in tokens
        assert "Tokens:" in tokens

        history = visible_text(
            focus_probe.run_slash(
                session,
                "/tokens recent 3",
                marker="Token history",
            )
        )
        assert "sessions with model calls" in history

        trace_listing = visible_text(
            focus_probe.run_slash(session, "/trace list", marker="trace files:")
        )
        assert "trace files: none" not in trace_listing
        assert "-http-response.json" in trace_listing
        assert "-structured.json" in trace_listing
        trace_path = _structured_trace_path(trace_listing)
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
