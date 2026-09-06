from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from openminion.modules.skill import proposal_queue
from openminion.modules.skill.proposal.catalog import (
    EmergentSkillCatalogAddition,
)
from openminion.modules.skill.proposal import SkillProposal, SkillProposalDraft
from openminion.modules.skill.proposal.queue import (
    apply_proposal,
    create_proposal,
    record_proposal_review,
    record_proposal_verification,
)
from openminion.modules.skill.interfaces import (
    SkillIngestAuthority,
    SkillVerificationEvidence,
)
from openminion.modules.skill.runtime.skill import Skill
from openminion.modules.skill.proposal.review import _RUNTIME_REVIEWER_IDS
from openminion.modules.skill.storage import SQLiteSkillStore


def _store(tmp_path: Path) -> SQLiteSkillStore:
    return SQLiteSkillStore(tmp_path / "skill.db", wal=False)


def _proposal(*, proposal_id: str = "wiring-1") -> SkillProposal:
    return SkillProposal(
        proposal_id=proposal_id,
        source_task_shape_ref="task_shape:wiring|wiring|wiring",
        proposed_skill_definition=SkillProposalDraft(
            name="wiring-playbook",
            display_name="Wiring Playbook",
            short_description="for wiring proof",
            tools=[],
            tags=["wiring"],
            risk_class="low",
            applies_to={"intents": ["wiring"], "steps": []},
            inputs_schema=[],
            verification_rules=[],
        ),
        skill_markdown="""---
name: wiring-playbook
description: Verify proposal wiring.
verification:
  - Confirm wiring output.
---
# Wiring Playbook

## Procedure

Verify proposal wiring.
""",
        evidence_refs=[],
        proposer_policy_id="wiring",
        proposed_at="",
    )


def test_record_proposal_review_calls_decide_skill_proposal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    calls: list[dict[str, Any]] = []
    real = proposal_queue.decide_skill_proposal

    def spy(*args: Any, **kwargs: Any):
        calls.append({"args": args, "kwargs": dict(kwargs)})
        return real(*args, **kwargs)

    monkeypatch.setattr(proposal_queue, "decide_skill_proposal", spy)

    store = _store(tmp_path)
    try:
        create_proposal(store, _proposal())
        record_proposal_review(
            store,
            proposal_id="wiring-1",
            reviewer_id="operator-x",
            review_policy_id="wiring",
            criterion_decisions=[
                {
                    "criterion_id": "fit",
                    "status": "accepted",
                    "comment": "ok",
                },
            ],
        )
    finally:
        store.close()
    assert len(calls) == 1
    assert calls[0]["kwargs"]["reviewer_id"] == "operator-x"


def test_apply_proposal_calls_ingest_and_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill = Skill(
        {
            "skill": {
                "sqlite_path": str(tmp_path / "skill.db"),
                "blob_root": str(tmp_path / "blob"),
                "fallback_root": str(tmp_path / "fallback"),
                "wal": False,
            }
        }
    )
    calls: list[str] = []
    real_ingest = skill.ingest_text
    real_admit = skill.admit_skill_version

    def ingest_spy(*args: Any, **kwargs: Any):
        calls.append("ingest")
        return real_ingest(*args, **kwargs)

    def admit_spy(*args: Any, **kwargs: Any):
        calls.append("admit")
        return real_admit(*args, **kwargs)

    monkeypatch.setattr(skill, "ingest_text", ingest_spy)
    monkeypatch.setattr(skill, "admit_skill_version", admit_spy)
    try:
        create_proposal(skill.store, _proposal())
        record_proposal_review(
            skill.store,
            proposal_id="wiring-1",
            reviewer_id="local:test",
            review_policy_id="wiring",
            criterion_decisions=[
                {
                    "criterion_id": "fit",
                    "status": "accepted",
                    "comment": "ok",
                },
            ],
        )
        record_proposal_verification(
            skill.store,
            proposal_id="wiring-1",
            operator_id="local:test",
            evidence=SkillVerificationEvidence(
                check="pytest",
                result="passed",
                evidence_ref="artifact://validation/pytest.txt",
            ),
        )
        addition = apply_proposal(
            skill,
            proposal_id="wiring-1",
            authority=SkillIngestAuthority.local_operator(
                surface="test.skill.apply",
                principal_id="local:test",
            ),
        )
    finally:
        skill.close()
    assert isinstance(addition, EmergentSkillCatalogAddition)
    assert calls == ["ingest", "admit"]


def test_runtime_reviewer_ids_set_is_unchanged_in_proposal_review() -> None:

    assert _RUNTIME_REVIEWER_IDS == frozenset(
        {"runtime", "system", "auto", "automatic", "self"}
    )


@pytest.mark.parametrize("runtime_id", sorted(_RUNTIME_REVIEWER_IDS))
def test_runtime_reviewer_ids_fail_closed_at_queue_seam(
    tmp_path: Path, runtime_id: str
) -> None:

    store = _store(tmp_path)
    try:
        create_proposal(store, _proposal())
        with pytest.raises(ValueError):
            record_proposal_review(
                store,
                proposal_id="wiring-1",
                reviewer_id=runtime_id,
                review_policy_id="wiring",
                criterion_decisions=[
                    {
                        "criterion_id": "fit",
                        "status": "accepted",
                        "comment": "should never persist",
                    },
                ],
            )
        # Confirm no review row exists.
        record = store.get_proposal(proposal_id="wiring-1")
        assert record is not None
        assert record["queue_state"] == "pending"
        assert record["review"] is None
    finally:
        store.close()


def test_proposal_queue_source_does_not_introduce_parallel_review_contract() -> None:

    source = Path(proposal_queue.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden_function_names = {
        "decide_proposal",
        "decide",
        "review_proposal",
        "apply_skill",
        "apply_emerged_skill",
    }
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert node.name not in forbidden_function_names, (
                f"proposal_queue defines parallel function {node.name!r}; "
                "queue layer must delegate to shipped contracts only"
            )
