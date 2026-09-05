from __future__ import annotations

from pathlib import Path

import pytest

from openminion.modules.skill.learning import ReplayProof, apply_proposal_with_replay
from openminion.modules.skill.proposal import SkillProposal, SkillProposalDraft
from openminion.modules.skill.proposal.queue import (
    create_proposal,
    get_proposal,
    record_proposal_review,
)
from openminion.modules.skill.runtime.skill import Skill
from openminion.modules.skill.storage import SQLiteSkillStore
from tests.skill.admission_helpers import ingest_file_and_admit

pytestmark = pytest.mark.e2e

SKILL_PATH = (
    Path(__file__).resolve().parents[3]
    / "examples"
    / "skills"
    / "repository-delivery"
    / "SKILL.md"
)


def test_repository_delivery_review_replay_apply_and_manual_use(tmp_path: Path) -> None:
    proposal_store = SQLiteSkillStore(tmp_path / "proposal.db", wal=False)
    skill = Skill(
        {
            "skill": {
                "sqlite_path": str(tmp_path / ".openminion" / "skill.db"),
                "wal": False,
                "known_tools": [],
                "default_status_filter": ["draft", "verified", "blessed"],
                "high_risk_status_filter": ["draft", "verified", "blessed"],
            }
        }
    )
    try:
        proposal = SkillProposal(
            proposal_id="repository-delivery-proposal",
            source_task_shape_ref="task-shape:repository-delivery",
            proposed_skill_definition=SkillProposalDraft(
                name="repository-delivery",
                display_name="Repository Delivery",
                short_description="Reviewed repository delivery procedure.",
                tags=["coding", "repository", "delivery"],
                risk_class="medium",
                applies_to={"intents": ["repository delivery"]},
                verification_rules=["repository_delivery_replay_passed"],
            ),
            evidence_refs=["evidence:repository-delivery-review"],
            proposer_policy_id="workflow_learning_review_first",
        )
        create_proposal(proposal_store, proposal)
        record_proposal_review(
            proposal_store,
            proposal_id=proposal.proposal_id,
            reviewer_id="operator-e2e",
            review_policy_id="workflow_learning_review",
            criterion_decisions=[
                {
                    "criterion_id": "procedure_only",
                    "status": "accepted",
                    "comment": "Commands remain repository-owned.",
                }
            ],
        )
        addition = apply_proposal_with_replay(
            proposal_store,
            proposal_id=proposal.proposal_id,
            current_catalog=[],
            replay_proof=ReplayProof(
                proof_id="repository-delivery-replay",
                proposal_id=proposal.proposal_id,
                shape_id="task-shape:repository-delivery",
                status="passed",
                evidence_refs=["replay:repository-delivery:passed"],
            ),
        )
        assert addition.added_skill_id == "emergent.repository-delivery"
        assert get_proposal(proposal_store, proposal_id=proposal.proposal_id)[
            "queue_state"
        ] == "applied"

        skill_id, version_hash, warnings = ingest_file_and_admit(
            skill,
            SKILL_PATH,
            name="repository-delivery",
        )
        assert skill_id == "repository-delivery"
        assert len(version_hash) == 64
        assert not any(item.startswith("lint.error:") for item in warnings)

        snippet, _ = skill.render_snippet(
            skill_id=skill_id,
            version_hash=version_hash,
            purpose="act",
            max_tokens=900,
        )
        for required in (
            "repository instructions",
            "artifact digest",
            "exact current approval",
            "uncertain remote result",
            "public TaskPlan",
        ):
            assert required in snippet
        markdown = SKILL_PATH.read_text(encoding="utf-8")
        for stop in (
            "repository is ambiguous",
            "required validation commands are missing",
            "exact approval is missing",
            "failed verification",
            "uncertain remote mutation",
        ):
            assert stop in markdown
        assert "make lint" not in markdown
        assert "pytest" not in markdown
    finally:
        skill.close()
        proposal_store.close()
