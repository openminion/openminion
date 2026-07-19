from __future__ import annotations

import pytest

from tests.e2e.tui.focus.harness.artifacts import artifact_root
from tests.e2e.tui.focus.harness.deep_smoke_matrix import (
    matrix_payload,
    matrix_rows,
    missing_required_items,
    write_matrix_artifact,
)

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(30)]


def test_deep_smoke_matrix_covers_required_break_surfaces(tmp_path) -> None:
    missing = missing_required_items()
    path = write_matrix_artifact(artifact_root(tmp_path))

    assert not missing, f"missing deep-smoke coverage: {sorted(missing)}"
    assert path.is_file()
    assert matrix_payload()["missing_required_items"] == []


def test_deep_smoke_rows_are_runnable_and_evidence_backed() -> None:
    allowed_execution = {"local", "live"}
    for row in matrix_rows():
        assert row.scenario_id
        assert row.summary
        assert row.execution in allowed_execution
        assert row.command.startswith(
            ("PYTHONDONTWRITEBYTECODE=1", "OPENMINION_LIVE_TUI_FOCUS_E2E=1")
        )
        assert row.owners
        assert row.covers
        assert row.evidence


def test_local_deep_smoke_matrix_has_a_bounded_runner_suite() -> None:
    local_rows = [row for row in matrix_rows() if row.execution == "local"]

    assert local_rows, "adversarial local smoke must not depend only on live providers"
    assert all(
        "pytest" in row.command or "run_tui_focus_e2e.py" in row.command
        for row in local_rows
    )
