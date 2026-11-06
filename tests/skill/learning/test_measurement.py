"""Paired transfer scoring and immutable measurement attachment contracts."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from openminion.modules.artifact.control import ArtifactCtl
from openminion.modules.skill.learning.measurement import (
    MeasurementIdentity,
    MeasurementLimits,
    MeasurementManifest,
    MeasurementReport,
    measurement_verdict,
    paired_trial_order,
    validate_measurement_attachment,
)
from openminion.modules.skill.models import stable_hash


@pytest.fixture
def campaign():
    identity = MeasurementIdentity(
        source_revision="revision",
        proposal_id="proposal",
        shape_id="shape",
        agent_id="author",
        candidate_hash="draft",
        skill_id="emergent.candidate",
        package_hash="version",
        corpus_digest="corpus",
        oracle_digest="oracle",
        model="model",
        provider="provider",
        config_digest="config",
        permission_digest="permission",
    )
    manifest = MeasurementManifest(
        identity=identity,
        acquisition_evidence_refs=["trace:development-1", "trace:development-2"],
        cases={"case-1": True, "case-2": True, "case-3": False},
        trial_order=paired_trial_order(["case-1", "case-2", "case-3"]),
    )
    observation = dict(
        trace_id="trace",
        trace_ref="artifact:trace",
        command_ref="artifact:command",
        result_ref="artifact:result",
        outcome="correct",
        tokens=100,
        tool_calls=2,
        elapsed_seconds=2,
    )
    report = MeasurementReport(
        identity=identity,
        manifest_ref="artifact:manifest",
        manifest_digest=stable_hash(manifest.model_dump(mode="json")),
        trials=[
            dict(
                key=key,
                applicable=manifest.cases[key.case_id],
                observation={
                    **observation,
                    "session_id": f"trial-{index}",
                    "trace_id": f"trace-{index}",
                    "trace_ref": f"artifact:trace-{index}",
                    "selected_skills": {identity.skill_id: identity.package_hash}
                    if key.arm == "candidate" and manifest.cases[key.case_id]
                    else {},
                },
            )
            for index, key in enumerate(manifest.trial_order)
        ],
        attempts=[
            dict(
                phase=phase,
                observation={
                    **observation,
                    "session_id": f"attempt-{index}",
                    "trace_id": f"attempt-trace-{index}",
                    "trace_ref": f"artifact:attempt-trace-{index}",
                },
            )
            for index, phase in enumerate(
                [
                    "acquisition",
                    "acquisition",
                    "authoring",
                    "replay",
                    "replay",
                    "replay",
                ]
            )
        ],
        verdict="no_demonstrated_gain",
    )
    return manifest, report


def test_gain_requires_an_external_correctness_improvement(campaign):
    manifest, report = campaign
    assert measurement_verdict(manifest, report) == "no_demonstrated_gain"
    report.trials[0].observation.outcome = "incorrect"
    assert measurement_verdict(manifest, report) == "demonstrated_gain"
    report.trials[1].observation.tokens = 251
    assert measurement_verdict(manifest, report) == "no_demonstrated_gain"


@pytest.mark.parametrize(
    "change",
    [
        "tokens",
        "missing",
        "duplicate",
        "contaminated",
        "budget",
        "identity",
        "session",
        "version",
    ],
)
def test_incomplete_or_incomparable_evidence_never_qualifies(campaign, change):
    manifest, report = campaign
    report.trials[0].observation.outcome = "incorrect"
    observation = report.trials[1].observation
    if change == "tokens":
        observation.tokens = None
    elif change == "missing":
        report.trials.pop()
    elif change == "duplicate":
        report.trials[-1] = report.trials[0]
    elif change == "contaminated":
        observation.contaminated = True
    elif change == "budget":
        observation.tool_calls = 41
    elif change == "identity":
        report.identity = report.identity.model_copy(
            update={"candidate_hash": "changed"}
        )
    elif change == "session":
        observation.session_id = report.trials[0].observation.session_id
    else:
        observation.selected_skills = {manifest.identity.skill_id: "changed-version"}
    assert measurement_verdict(manifest, report) == "inconclusive"


def test_known_regression_survives_incomplete_other_trials(campaign):
    manifest, report = campaign
    report.trials[1].observation.outcome = "incorrect"
    report.trials.pop()
    assert measurement_verdict(manifest, report) == "regression"


@pytest.mark.parametrize("change", ["irrelevant_selection", "intervention"])
def test_gain_requires_appropriate_use_without_added_intervention(campaign, change):
    manifest, report = campaign
    report.trials[0].observation.outcome = "incorrect"
    if change == "irrelevant_selection":
        report.trials[5].observation.selected_skills = {
            manifest.identity.skill_id: manifest.identity.package_hash
        }
    else:
        report.trials[1].observation.interventions = 1
    assert measurement_verdict(manifest, report) == "no_demonstrated_gain"


def test_design_cannot_shrink_or_raise_limits(campaign):
    manifest, _ = campaign
    with pytest.raises(ValidationError, match="two applicable"):
        MeasurementManifest.model_validate(
            {**manifest.model_dump(), "cases": {"one-case": True}}
        )
    with pytest.raises(ValidationError):
        MeasurementLimits(turn_tokens=40001)
    with pytest.raises(ValidationError, match="trial order"):
        MeasurementManifest.model_validate(
            {
                **manifest.model_dump(),
                "trial_order": list(reversed(manifest.trial_order)),
            }
        )


@pytest.fixture
def artifacts(tmp_path, campaign):
    with ArtifactCtl(
        {
            "blob_store": {"root_dir": str(tmp_path / "artifact")},
            "index": {"sqlite_path": str(tmp_path / "artifact" / "index.db")},
            "views": {"auto_generate": []},
        }
    ) as ctl:
        _, report = campaign
        for observation in [t.observation for t in report.trials] + [
            a.observation for a in report.attempts
        ]:
            for field in ("trace_ref", "command_ref", "result_ref"):
                ref = ctl.ingest_bytes(
                    f"{observation.session_id}:{field}".encode(),
                    mime="text/plain",
                    agent_id="evaluator",
                ).ref
                setattr(observation, field, ref)
        yield ctl


def test_attachment_binds_independent_manifest_and_verdict(campaign, artifacts):
    manifest, report = campaign
    manifest_ref = artifacts.ingest_bytes(
        manifest.model_dump_json().encode(),
        mime="application/json",
        agent_id="evaluator",
    ).ref
    report.manifest_ref = manifest_ref
    report_ref = artifacts.ingest_bytes(
        report.model_dump_json().encode(), mime="application/json", agent_id="evaluator"
    ).ref
    retained = validate_measurement_attachment(
        artifacts,
        measurement_ref=report_ref,
        manifest_ref=manifest_ref,
        identity={"candidate_hash": "draft"},
        evaluator_id="evaluator",
    )
    assert retained.verdict == "no_demonstrated_gain"
    with pytest.raises(ValueError, match="candidate identity"):
        validate_measurement_attachment(
            artifacts,
            measurement_ref=report_ref,
            manifest_ref=manifest_ref,
            identity={"candidate_hash": "changed"},
            evaluator_id="evaluator",
        )
    report.verdict = "demonstrated_gain"
    forged_ref = artifacts.ingest_bytes(
        report.model_dump_json().encode(), mime="application/json", agent_id="evaluator"
    ).ref
    with pytest.raises(ValueError, match="verdict"):
        validate_measurement_attachment(
            artifacts,
            measurement_ref=forged_ref,
            manifest_ref=manifest_ref,
            identity={"candidate_hash": "draft"},
            evaluator_id="evaluator",
        )
    report.trials.pop()
    report.verdict = "inconclusive"
    missing_ref = artifacts.ingest_bytes(
        report.model_dump_json().encode(), mime="application/json", agent_id="evaluator"
    ).ref
    with pytest.raises(ValueError, match="complete frozen paired cases"):
        validate_measurement_attachment(
            artifacts,
            measurement_ref=missing_ref,
            manifest_ref=manifest_ref,
            identity={"candidate_hash": "draft"},
            evaluator_id="evaluator",
        )


@pytest.mark.parametrize("phase", ["authoring", "replay"])
def test_failed_prerequisite_does_not_qualify(campaign, phase):
    manifest, report = campaign
    report.trials[0].observation.outcome = "incorrect"
    next(
        a for a in report.attempts if a.phase == phase
    ).observation.outcome = "incorrect"
    assert measurement_verdict(manifest, report) == "inconclusive"


def test_retained_ready_evidence_does_not_invent_acquisition_turns(campaign):
    manifest, report = campaign
    report.attempts = [a for a in report.attempts if a.phase != "acquisition"]
    report.trials[0].observation.outcome = "incorrect"
    assert measurement_verdict(manifest, report) == "demonstrated_gain"


def test_rejected_attachment_cannot_mutate_existing_proof(
    campaign, artifacts, tmp_path, monkeypatch, capsys
):
    from openminion.modules.skill.learning.replay import (
        ReplayEvaluationResult,
        proposal_draft_hash,
    )
    from openminion.modules.skill.proposal.base import (
        SkillProposal,
        SkillProposalDraft,
        SkillProcedure,
    )
    from openminion.modules.skill.proposal.catalog import prepare_emergent_skill
    from openminion.modules.skill.proposal.queue import (
        ProposalQueueError,
        create_proposal,
        get_proposal,
        record_proposal_review,
        record_replay_proof,
    )
    from openminion.modules.skill.storage import SQLiteSkillStore

    manifest, report = campaign
    proposal = SkillProposal(
        proposal_id="proposal",
        source_task_shape_ref="shape",
        agent_id="author",
        requires_replay_proof=True,
        evidence_refs=manifest.acquisition_evidence_refs,
        proposed_skill_definition=SkillProposalDraft(
            name="candidate",
            short_description="A reviewed local procedure.",
            procedure=SkillProcedure(
                when_to_use=["Local coding tasks"],
                do_not_use=["Read-only tasks"],
                steps=["Inspect the shared boundary before editing."],
                validation=["Run behavioral tests."],
            ),
        ),
    )
    store = SQLiteSkillStore(tmp_path / "skill.db", wal=False)
    try:
        proposal = SkillProposal.model_validate(
            create_proposal(store, proposal)["proposal"]
        )
        review = record_proposal_review(
            store,
            proposal_id=proposal.proposal_id,
            reviewer_id="independent-reviewer",
            review_policy_id="independent-review",
            criterion_decisions=[
                {
                    "criterion_id": "procedure",
                    "status": "accepted",
                    "comment": "Reviewed content and scope.",
                }
            ],
        )
        _, package = prepare_emergent_skill(
            review,
            skill_definition=proposal.proposed_skill_definition,
            proposal=proposal,
        )
        identity = manifest.identity.model_copy(
            update={
                "candidate_hash": proposal_draft_hash(proposal),
                "skill_id": package.skill_id,
                "package_hash": package.version_hash,
            }
        )
        manifest.identity = report.identity = identity
        manifest_ref = artifacts.ingest_bytes(
            manifest.model_dump_json().encode(),
            mime="application/json",
            agent_id="evaluator",
        ).ref
        report.manifest_ref = manifest_ref
        report.manifest_digest = stable_hash(manifest.model_dump(mode="json"))
        report_ref = artifacts.ingest_bytes(
            report.model_dump_json().encode(),
            mime="application/json",
            agent_id="evaluator",
        ).ref
        evaluation = ReplayEvaluationResult(
            proof_id="proof",
            proposal_id=proposal.proposal_id,
            shape_id="shape",
            candidate_hash=identity.candidate_hash,
            evaluator_id="evaluator",
            status="passed",
            measurement_ref=report_ref,
        )
        result_ref = artifacts.ingest_bytes(
            evaluation.model_dump_json().encode(),
            mime="application/json",
            agent_id="evaluator",
        ).ref
        with pytest.raises(ProposalQueueError, match="independently frozen"):
            record_replay_proof(
                store,
                proposal_id="proposal",
                result_ref=result_ref,
                artifactctl=artifacts,
            )
        assert get_proposal(store, proposal_id="proposal")["replay_proof"] is None
        retained = record_replay_proof(
            store,
            proposal_id="proposal",
            result_ref=result_ref,
            artifactctl=artifacts,
            measurement_manifest_ref=manifest_ref,
        )
        assert (
            retained["status"] == "passed" and retained["measurement_ref"] == report_ref
        )
        report.verdict = "demonstrated_gain"
        forged_ref = artifacts.ingest_bytes(
            report.model_dump_json().encode(),
            mime="application/json",
            agent_id="evaluator",
        ).ref
        evaluation.measurement_ref = forged_ref
        forged_result = artifacts.ingest_bytes(
            evaluation.model_dump_json().encode(),
            mime="application/json",
            agent_id="evaluator",
        ).ref
        with pytest.raises(ValueError, match="verdict"):
            record_replay_proof(
                store,
                proposal_id="proposal",
                result_ref=forged_result,
                artifactctl=artifacts,
                measurement_manifest_ref=manifest_ref,
            )
        record = get_proposal(store, proposal_id="proposal")
        assert record["replay_proof"] == retained
        assert (
            record["queue_state"] == "reviewed" and record["applied_addition"] is None
        )
        assert store.get_skill_package(package.skill_id, package.version_hash) is None
        from openminion.modules.skill.cli import main

        config = tmp_path / "skill.json"
        config.write_text(
            json.dumps(
                {"skill": {"sqlite_path": str(tmp_path / "skill.db"), "wal": False}}
            )
        )
        monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path))
        assert main(["--config", str(config), "proposal-inspect", "proposal"]) == 0
        inspected = json.loads(capsys.readouterr().out)["proposal"]
        assert inspected["measurement"] == {
            "ref": report_ref,
            "verdict": "no_demonstrated_gain",
        }
        assert inspected["replay_proof"]["status"] == "passed"
    finally:
        store.close()


@pytest.mark.parametrize("field", ["trace_id", "trace_ref"])
def test_reused_trace_cannot_become_a_fresh_trial(campaign, field):
    manifest, report = campaign
    report.trials[0].observation.outcome = "incorrect"
    setattr(
        report.trials[1].observation,
        field,
        getattr(report.trials[0].observation, field),
    )
    assert measurement_verdict(manifest, report) == "inconclusive"


def test_unused_candidate_does_not_demonstrate_transfer(campaign):
    manifest, report = campaign
    report.trials[0].observation.outcome = "incorrect"
    for trial in report.trials:
        trial.observation.selected_skills.clear()
    assert measurement_verdict(manifest, report) == "no_demonstrated_gain"


def test_measurement_cas_owner_matches_result_evaluator(campaign, artifacts):
    manifest, report = campaign
    manifest_ref = artifacts.ingest_bytes(
        manifest.model_dump_json().encode(),
        mime="application/json",
        agent_id="wrong-evaluator",
    ).ref
    report.manifest_ref = manifest_ref
    report_ref = artifacts.ingest_bytes(
        report.model_dump_json().encode(), mime="application/json", agent_id="evaluator"
    ).ref
    with pytest.raises(ValueError, match="provenance"):
        validate_measurement_attachment(
            artifacts,
            measurement_ref=report_ref,
            manifest_ref=manifest_ref,
            identity={},
            evaluator_id="evaluator",
        )


def test_deleted_trial_evidence_cannot_be_attached(campaign, artifacts):
    manifest, report = campaign
    manifest_ref = artifacts.ingest_bytes(
        manifest.model_dump_json().encode(),
        mime="application/json",
        agent_id="evaluator",
    ).ref
    report.manifest_ref = manifest_ref
    report_ref = artifacts.ingest_bytes(
        report.model_dump_json().encode(), mime="application/json", agent_id="evaluator"
    ).ref
    artifacts.delete(report.trials[0].observation.result_ref)
    with pytest.raises(ValueError, match="trial evidence is deleted"):
        validate_measurement_attachment(
            artifacts,
            measurement_ref=report_ref,
            manifest_ref=manifest_ref,
            identity={},
            evaluator_id="evaluator",
        )
