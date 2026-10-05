from __future__ import annotations

import pytest

from tests.e2e.cli.focus.conftest import require_live_focus
from tests.e2e.cli.focus.harness import FocusProbe, FocusScenario

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(1800)]


def test_live_focus_itinerary_research_preserves_followup_context(
    focus_probe: FocusProbe,
) -> None:
    require_live_focus()
    probe = focus_probe.for_workdir(
        focus_probe.workdir,
        include_project_context=False,
    )
    with probe.session() as session:
        probe.wait_ready(session)
        probe.run_turn(
            session,
            FocusScenario(
                scenario_id="itinerary_research",
                prompt=(
                    "For Sunday, October 11, 2026, find three train routes from "
                    "Nikaido Station in Nara to Hakata Station, including options "
                    "that board the Shinkansen at Kyoto and Shin-Osaka. Use current "
                    "web evidence. For each route show every leg, departure and "
                    "arrival times, transfer waits, total duration, and per-person "
                    "fare in JPY. Distinguish timetable evidence from live disruption "
                    "or seat-availability evidence, keep source conflicts visible, "
                    "and include source URLs."
                    " Label the alternatives Route 1, Route 2, and Route 3."
                ),
                expected_markers=(
                    "Route 1",
                    "Route 2",
                    "Route 3",
                    "Kyoto",
                    "Shin-Osaka|Shin Osaka",
                    "Hakata",
                    "https://|http://",
                ),
                forbidden_transcript_markers=("Project queued:",),
                timeout=1200,
                requires_approval=True,
                max_auto_approvals=6,
                approval_reply="session",
                include_project_context=False,
                max_auto_continuations=3,
            ),
        )
        probe.run_turn(
            session,
            FocusScenario(
                scenario_id="itinerary_shopping_followup",
                prompt=(
                    "Keep the Kyoto route and revise its pre-Shinkansen legs so I "
                    "have at least 90 minutes to shop at Kyoto Station. Show the "
                    "updated leg-by-leg timeline, shopping window, transfer buffer, "
                    "Hakata arrival, fare, and source URLs."
                ),
                expected_markers=(
                    "Nikaido",
                    "Kyoto",
                    "90 minutes|90-min|100-minute|1 hour 30|1h 30|1.5 hours",
                    "Hakata",
                    "https://|http://",
                ),
                forbidden_transcript_markers=("Project queued:",),
                timeout=900,
                requires_approval=True,
                max_auto_approvals=4,
                approval_reply="session",
                include_project_context=False,
                max_auto_continuations=2,
            ),
        )
