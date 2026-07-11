from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from openminion.modules.brain.runtime.recurrence import (
    RecurringTaskShape,
    TaskShapeRecurrenceWindow,
)
from openminion.modules.skill.config import SkillConfig
from openminion.modules.skill.proposal import (
    SkillProposal,
)
from openminion.modules.skill.proposal.queue import (
    apply_proposal,
    create_proposal,
    list_proposals,
    record_proposal_review,
)
from openminion.modules.skill.storage import SQLiteSkillStore
from openminion.modules.skill.suggestion import (
    run_suggestion_surface_pass,
    suggestion_status,
)
from openminion.modules.memory.runtime.skill_promotion import (
    run_skill_promotion_cadence_once,
)


@dataclass
class _PilotMemoryAPI:
    shapes: list[RecurringTaskShape]
    catalog: list[dict[str, Any]]
    store: SQLiteSkillStore

    def get_recurring_task_shapes(self) -> list[RecurringTaskShape]:
        return list(self.shapes)

    def get_current_skill_catalog(self) -> list[dict[str, Any]]:
        return list(self.catalog)

    def record_promotion_proposal(self, proposal: SkillProposal) -> None:
        # Pilot wiring: the cadence's recorder hook persists into SPRQ.
        create_proposal(self.store, proposal)

    def record_promotion_review(self, review: Any) -> None:
        return


def _synthetic_shapes() -> list[RecurringTaskShape]:
    return [
        RecurringTaskShape(
            task_shape_ref="task_shape:scsp_pilot_a|live|news",
            strategy_id="scsp_pilot_a",
            capability_category="live",
            intent_category="news",
            recurrence_count=7,
            evidence_window=TaskShapeRecurrenceWindow().model_dump(mode="json"),
        ),
        RecurringTaskShape(
            task_shape_ref="task_shape:scsp_pilot_b|dev|tests",
            strategy_id="scsp_pilot_b",
            capability_category="dev",
            intent_category="tests",
            recurrence_count=5,
            evidence_window=TaskShapeRecurrenceWindow().model_dump(mode="json"),
        ),
        RecurringTaskShape(
            task_shape_ref="task_shape:scsp_pilot_c|ops|deploy",
            strategy_id="scsp_pilot_c",
            capability_category="ops",
            intent_category="deploy",
            recurrence_count=4,
            evidence_window=TaskShapeRecurrenceWindow().model_dump(mode="json"),
        ),
    ]


@pytest.fixture
def pilot_env(tmp_path: Path):
    store = SQLiteSkillStore(tmp_path / "pilot.db", wal=False)
    config = SkillConfig(
        promotion_cadence_enabled=True,
        promotion_cadence_success_threshold=1,
        promotion_cadence_utility_threshold=0.0,
    )
    memory = _PilotMemoryAPI(
        shapes=_synthetic_shapes(),
        catalog=[],
        store=store,
    )
    try:
        yield store, config, memory
    finally:
        store.close()


def test_scsp04_demonstrative_pilot_loop(pilot_env) -> None:
    store, config, memory = pilot_env
    result = run_skill_promotion_cadence_once(
        config=config,
        memory_api=memory,
        audit_sink=None,
        force_enabled=True,
    )
    assert result.enabled is True
    assert result.dry_run is False
    assert result.report is not None
    cadence_proposals = result.report.proposals_drafted
    assert cadence_proposals == 3

    surface_pass = run_suggestion_surface_pass(store)
    assert len(surface_pass.surfaced) == 3
    assert surface_pass.auto_dismissed == []

    pending = list_proposals(store, queue_state="pending")
    assert len(pending) == 3
    outcomes = ["accepted", "rejected", "deferred"]
    for proposal_row, outcome in zip(
        sorted(pending, key=lambda row: row["proposal_id"]),
        outcomes,
        strict=True,
    ):
        record_proposal_review(
            store,
            proposal_id=proposal_row["proposal_id"],
            reviewer_id="operator-pilot",
            review_policy_id="scsp_pilot_v1",
            criterion_decisions=[
                {
                    "criterion_id": "fit",
                    "status": outcome,
                    "comment": f"pilot outcome: {outcome}",
                },
            ],
        )

    accepted_rows = [
        row
        for row in list_proposals(store, queue_state="reviewed")
        if (row.get("review") or {}).get("status") == "accepted"
    ]
    assert len(accepted_rows) == 1
    addition = apply_proposal(
        store,
        proposal_id=accepted_rows[0]["proposal_id"],
        current_catalog=[],
    )
    assert addition.added_skill_id.startswith("emergent.")

    status_payload = suggestion_status(store).to_dict()
    assert status_payload["surfaced_count"] == 3
    assert status_payload["accepted_count"] == 1
    assert status_payload["rejected_count"] == 1
    assert status_payload["deferred_count"] == 1
    assert status_payload["auto_dismissed_count"] == 0
    assert status_payload["pending_count"] == 0


def test_scsp04_pilot_anti_spam_blocks_repeat_signature(pilot_env) -> None:
    store, config, memory = pilot_env
    for _ in range(2):
        result = run_skill_promotion_cadence_once(
            config=config,
            memory_api=memory,
            audit_sink=None,
            force_enabled=True,
        )
    assert result.enabled is True
    assert len(list_proposals(store, queue_state="pending")) == 3

    first_pass = run_suggestion_surface_pass(store)
    assert len(first_pass.surfaced) == 3

    second_pass = run_suggestion_surface_pass(store)
    assert second_pass.surfaced == []
    assert len(second_pass.auto_dismissed) >= 1
    status_payload = suggestion_status(store).to_dict()
    assert status_payload["surfaced_count"] == 3
    assert status_payload["auto_dismissed_count"] >= 1
