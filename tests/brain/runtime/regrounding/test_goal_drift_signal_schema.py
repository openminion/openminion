from __future__ import annotations

import pytest

from openminion.modules.brain.schemas import GoalDriftSignal
from openminion.modules.brain.schemas.goals import GoalDriftSignalKind  # noqa: F401


def _make_signal(**overrides):
    payload = dict(
        signal_id="sig-1",
        goal_id="goal-abc",
        kind="actions_diverge_from_criteria",
        description="actions diverged",
        detected_at="2026-05-26T00:00:00Z",
        evidence={"recent_action_count": 5},
    )
    payload.update(overrides)
    return GoalDriftSignal(**payload)


def test_signal_constructs_with_all_four_closed_set_kinds():

    for kind in (
        "actions_diverge_from_criteria",
        "inaction_against_criteria",
        "objective_substitution",
        "mission_type_drift",
    ):
        sig = _make_signal(kind=kind)
        assert sig.kind == kind


def test_signal_rejects_unknown_kind_value():

    with pytest.raises(Exception):
        _make_signal(kind="unknown_kind_value")


def test_signal_is_frozen():

    sig = _make_signal()
    with pytest.raises(Exception):
        sig.description = "mutated"


def test_signal_requires_non_empty_required_fields():

    for field_name in ("signal_id", "goal_id", "description", "detected_at"):
        with pytest.raises(Exception):
            _make_signal(**{field_name: ""})


def test_signal_evidence_defaults_to_empty_dict():

    sig = GoalDriftSignal(
        signal_id="sig",
        goal_id="g",
        kind="inaction_against_criteria",
        description="no progress",
        detected_at="2026-05-26",
    )
    assert sig.evidence == {}
