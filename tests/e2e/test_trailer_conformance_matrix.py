from __future__ import annotations

from tests.e2e.runners.run_trailer_conformance_matrix import (
    _aggregate_by_provider,
    _summarize_session,
)

import pytest

pytestmark = pytest.mark.e2e


def test_matrix_runner_reports_per_source_breakdown() -> None:
    session = _summarize_session(
        "session-1",
        [
            {
                "event_type": "trailer.expected",
                "lanes": ["macc", "apd"],
                "route": "adaptive_final",
            },
            {
                "event_type": "trailer.emitted",
                "lanes": ["macc"],
                "route": "adaptive_final",
                "sources": {"macc": ["structured_field"]},
            },
            {
                "event_type": "task_plan.declared",
                "lanes": [],
                "route": "",
                "source": "plan_tool",
            },
            {
                "event_type": "task_plan.step_completed",
                "lanes": [],
                "route": "",
                "source": "trailer",
            },
        ],
        "minimax-m2-7",
    )

    assert session["lane_stats"]["macc"] == {"expected": 1, "emitted": 1}
    assert session["lane_stats"]["apd"] == {"expected": 1, "emitted": 2}
    assert session["source_stats"]["macc"]["structured_field"] == 1
    assert session["source_stats"]["apd"]["plan_tool"] == 1
    assert session["source_stats"]["apd"]["trailer"] == 1

    aggregate = _aggregate_by_provider([session])
    provider = aggregate["minimax-m2-7"]

    assert provider["lane_rates"]["macc"]["rate"] == 1
    assert provider["source_rates"]["macc"]["structured_field"] == 1
    assert provider["source_rates"]["apd"]["plan_tool"] == 1
    assert provider["source_rates"]["apd"]["trailer"] == 1
