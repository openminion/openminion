"""Stored evidence, review, and package identity for authored learning proposals."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from openminion.modules.skill.learning.proposer import stage_authored_skill_proposal
from openminion.modules.skill.learning.runtime import observe_runtime_workflow
from openminion.modules.skill.learning.replay import proposal_draft_hash
from openminion.modules.skill.proposal.base import (
    SkillProcedure,
    SkillProposal,
    SkillProposalDraft,
)
from openminion.modules.skill.proposal.catalog import (
    SkillEmergenceReviewGateError,
    apply_emergent_skill,
    prepare_emergent_skill,
)
from openminion.modules.skill.proposal.queue import (
    ProposalQueueError,
    apply_proposal,
    get_proposal,
    record_proposal_review,
)
from openminion.modules.skill.storage import SQLiteSkillStore


# Serialized with the pre-change models at 5ccfa1af4; keep the payload and hash fixed.
_LEGACY_PROPOSAL = """{
  "proposal_id":"legacy-stored",
  "source_task_shape_ref":"workflow_shape:legacy",
  "proposed_skill_definition":{
    "name":"legacy-repair","display_name":"",
    "short_description":"Repair a local defect.",
    "tools":[],"tags":[],"risk_class":"low","applies_to":{},
    "inputs_schema":[],"verification_rules":["Run the regression."]
  },
  "evidence_refs":[],"proposer_policy_id":"",
  "proposed_at":"2026-11-01T00:00:00Z","requires_replay_proof":true
}"""
_LEGACY_PROOF = """{
  "proof_id":"legacy-proof","proposal_id":"legacy-stored",
  "shape_id":"workflow_shape:legacy",
  "candidate_hash":"49654d236b8eaf2c286b8987657568fbf95503062ce17fb035e94e867c447f25",
  "evaluator_id":"legacy-evaluator","result_ref":"replay:legacy-result",
  "status":"passed","command":"pytest tests",
  "evidence_refs":["replay:legacy-result"],"summary":""
}"""


@pytest.fixture
def store(tmp_path: Path):
    instance = SQLiteSkillStore(tmp_path / "skill.db", wal=False)
    yield instance
    instance.close()


@pytest.fixture
def draft() -> SkillProposalDraft:
    return SkillProposalDraft(
        name="repair-local-code",
        short_description="Repair a local defect and verify the changed behavior.",
        tools=["file.write", "exec.run"],
        risk_class="medium",
        applies_to={"intents": ["modify"], "steps": ["act"]},
        procedure=SkillProcedure(
            when_to_use=["A local code defect has a reproducible example."],
            do_not_use=["Do not apply to remote services."],
            steps=[
                "Reproduce the defect.\n# Keep the failing example.",
                "Fix its owner.",
            ],
            validation=["Run the example and focused regression tests."],
        ),
    )


def _observe(store, agent_id="agent-a", count=2):
    for index in range(count):
        result = observe_runtime_workflow(
            store,
            agent_id=agent_id,
            source_run_ref=f"run-{index}",
            intent_category="modify",
            capability_category="code",
            tool_names=["file.write", "exec.run"],
        )
    return result.shape_id


def _review(store, proposal, reviewer_id="reviewer"):
    return record_proposal_review(
        store,
        proposal_id=proposal.proposal_id,
        reviewer_id=reviewer_id,
        review_policy_id="authored-review",
        criterion_decisions=[
            {"criterion_id": "fit", "status": "accepted", "comment": "Reviewed."}
        ],
    )


@pytest.mark.parametrize("field", ["when_to_use", "do_not_use", "steps", "validation"])
@pytest.mark.parametrize("invalid", [[], [""], ["   "], [7]])
def test_procedure_requires_nonempty_text(draft, field, invalid):
    payload = draft.procedure.model_dump()
    payload[field] = invalid
    with pytest.raises(ValidationError):
        SkillProcedure.model_validate(payload)


def test_authored_proposal_identity_is_agent_shape_and_content(store, draft):
    shape_id = _observe(store)
    first = stage_authored_skill_proposal(
        store=store, agent_id="agent-a", shape_id=shape_id, draft=draft
    )
    _observe(store, count=3)
    repeated = stage_authored_skill_proposal(
        store=store, agent_id="agent-a", shape_id=shape_id, draft=draft
    )
    changed = stage_authored_skill_proposal(
        store=store,
        agent_id="agent-a",
        shape_id=shape_id,
        draft=draft.model_copy(
            update={"short_description": "A revised procedure card."}
        ),
    )
    _observe(store, agent_id="agent-b")
    other_agent = stage_authored_skill_proposal(
        store=store, agent_id="agent-b", shape_id=shape_id, draft=draft
    )
    assert first.proposal == repeated.proposal
    assert first.proposal.proposed_at
    assert first.queue_record["created_now"] is True
    assert repeated.queue_record["created_now"] is False
    assert first.proposal.evidence_refs == ["trace:run-0", "trace:run-1"]
    assert first.proposal.proposed_skill_definition == draft
    assert (
        len({item.proposal.proposal_id for item in (first, changed, other_agent)}) == 3
    )
    packages = []
    for item in (first, changed, other_agent):
        review = _review(store, item.proposal)
        _, package = prepare_emergent_skill(
            review,
            skill_definition=item.proposal.proposed_skill_definition,
            proposal=item.proposal,
        )
        packages.append(package)
    assert len({package.skill_id for package in packages}) == 3


def test_authored_proposal_requires_same_agent_ready_shape(store, draft):
    shape_id = _observe(store, count=1)
    result = stage_authored_skill_proposal(
        store=store, agent_id="agent-a", shape_id=shape_id, draft=draft
    )
    assert result.status == "not_ready"
    with pytest.raises(ValueError, match="not stored"):
        stage_authored_skill_proposal(
            store=store, agent_id="agent-b", shape_id=shape_id, draft=draft
        )
    assert store.list_proposals() == []


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"risk_class": "low"}, "risk"),
        ({"tools": ["cloud.delete"]}, "tools"),
        ({"procedure": None}, "procedure"),
        ({"name": " "}, "name"),
        ({"short_description": " "}, "short_description"),
    ],
)
def test_authored_proposal_rejects_invalid_draft_without_write(
    store, draft, change, message
):
    shape_id = _observe(store)
    with pytest.raises(ValueError, match=message):
        stage_authored_skill_proposal(
            store=store,
            agent_id="agent-a",
            shape_id=shape_id,
            draft=draft.model_copy(update=change),
        )
    assert store.list_proposals() == []


def test_prepared_authored_package_is_stable_and_preserves_sections(
    store, draft, monkeypatch
):
    staged = stage_authored_skill_proposal(
        store=store, agent_id="agent-a", shape_id=_observe(store), draft=draft
    )
    proposal = staged.proposal
    with pytest.raises(ProposalQueueError, match="independent reviewer"):
        _review(store, proposal, reviewer_id="agent-a")
    assert get_proposal(store, proposal_id=proposal.proposal_id)["review"] is None
    review = _review(store, proposal)
    addition, package = prepare_emergent_skill(
        review, skill_definition=draft, proposal=proposal
    )
    monkeypatch.setattr(
        "openminion.modules.skill.proposal.catalog.utc_now_iso", lambda: "later"
    )
    repeated_addition, catalog = apply_emergent_skill(
        review,
        skill_definition=draft,
        proposal=proposal,
        catalog=[
            {
                "skill_id": "operator-existing",
                "name": draft.name,
                "source": "operator_declared",
            }
        ],
    )
    assert repeated_addition == addition
    assert catalog[-1].to_dict() == package.to_dict()
    assert package.created_at == package.updated_at == proposal.proposed_at
    assert package.scope == "agent"
    assert package.agent_id == "agent-a"
    assert "Reproduce the defect." in package.sections["procedure"]
    assert "# Keep the failing example." in package.sections["procedure"]
    assert draft.procedure.validation[0] in package.sections["verification"]
    assert draft.procedure.do_not_use[0] in package.sections["pitfalls"]
    assert store.get_skill_package(package.skill_id, package.version_hash) is None
    with pytest.raises(SkillEmergenceReviewGateError, match="persisted proposal"):
        prepare_emergent_skill(review, skill_definition=draft)
    with pytest.raises(SkillEmergenceReviewGateError, match="independent reviewer"):
        prepare_emergent_skill(
            review.model_copy(update={"reviewer_id": "agent-a"}),
            skill_definition=draft,
            proposal=proposal,
        )


def test_prechange_persisted_proposal_and_proof_still_apply(store):
    proposal = SkillProposal.model_validate_json(_LEGACY_PROPOSAL)
    store.create_proposal(
        proposal_id=proposal.proposal_id,
        source_task_shape_ref=proposal.source_task_shape_ref,
        proposer_policy_id=proposal.proposer_policy_id,
        proposed_at=proposal.proposed_at,
        proposal_json=_LEGACY_PROPOSAL,
        created_at=proposal.proposed_at,
    )
    _review(store, proposal)
    store.record_proposal_replay_proof(
        proposal_id=proposal.proposal_id,
        replay_proof_json=_LEGACY_PROOF,
        recorded_at=proposal.proposed_at,
    )
    assert proposal_draft_hash(proposal) == json.loads(_LEGACY_PROOF)["candidate_hash"]
    addition = apply_proposal(
        store, proposal_id=proposal.proposal_id, current_catalog=[]
    )
    assert addition.added_skill_id == "emergent.legacy-repair"
    assert addition.replay_proof["measurement_ref"] is None
    package = store.get_skill_package(addition.added_skill_id, addition.version_hash)
    assert package["scope"] == "global"
    assert package["sections"] == {"summary": "Repair a local defect."}
