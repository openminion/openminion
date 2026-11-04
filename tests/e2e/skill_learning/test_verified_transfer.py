"""Offline checks for experimental accounting and isolated campaign state."""

from copy import deepcopy
from hashlib import sha256
import subprocess

import pytest

from openminion.modules.artifact.control import ArtifactCtl
from openminion.modules.skill.learning.measurement import (
    MeasurementManifest,
    paired_trial_order,
)
from openminion.modules.skill.learning.proposer import stage_authored_skill_proposal
from openminion.modules.skill.learning.runtime import observe_runtime_workflow
from openminion.modules.skill.proposal.base import SkillProposalDraft, SkillProcedure
from openminion.modules.skill.runtime.skill import Skill
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.runtime.policy import Policy
from tests.e2e.cli.focus.test_live_skill_confirmed_save import _skill_config
from tests.e2e.skill_learning.live_evidence import (
    canonical_usage,
    focus_turn,
    retain,
    write_json,
)
from tests.e2e.skill_learning.live_transfer import TransferCampaign
from tests.helpers.runtime_roots import configure_runtime_roots, RUNTIME_ROOT_ENV_VARS


def _events():
    return [
        {
            "event_type": "llm.call.completed",
            "data": {
                "usage": {"input_tokens": 90, "output_tokens": 10},
                "trace_id": "trace-a",
            },
        },
        {
            "event_type": "chat.phase_timing",
            "data": {
                "provider_input_tokens": 90,
                "provider_output_tokens": 10,
                "provider_calls_total": 1,
                "provider_usage_complete": True,
                "provider_attempts": [
                    {"outcome": "ok", "provider": "fixture", "model": "model-a"},
                ],
            },
        },
    ]


def test_usage_counts_the_canonical_phase_once():
    assert canonical_usage(_events(), provider="fixture", model="model-a") == 100


def test_complete_phase_counts_sparse_adaptive_and_auxiliary_calls():
    events = _events()
    phase = events[-1]["data"]
    phase.update(
        provider_input_tokens=36319, provider_output_tokens=963, provider_calls_total=7
    )
    phase["provider_attempts"] = [dict(phase["provider_attempts"][0]) for _ in range(7)]
    assert canonical_usage(events, provider="fixture", model="model-a") == 37282


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider_usage_complete", None),
        ("provider_usage_complete", False),
        ("provider_input_tokens", None),
        ("provider_output_tokens", None),
        ("provider_calls_total", 2),
        ("provider_attempts", []),
    ],
)
def test_unknown_physical_usage_cannot_become_zero(field, value):
    events = _events()
    events[-1]["data"][field] = value
    assert canonical_usage(events, provider="fixture", model="model-a") is None


@pytest.mark.parametrize(
    ("field", "value"),
    [("outcome", "error"), ("provider", "other"), ("model", "other")],
)
def test_attempt_identity_and_success_must_match(field, value):
    events = _events()
    events[-1]["data"]["provider_attempts"][0][field] = value
    assert canonical_usage(events, provider="fixture", model="model-a") is None


def test_old_complete_looking_record_stays_unavailable():
    events = _events()
    events[-1]["data"].pop("provider_usage_complete")
    assert canonical_usage(events, provider="fixture", model="model-a") is None


def test_retained_observations_have_distinct_session_identity_and_owner(
    tmp_path, monkeypatch
):
    for variable in RUNTIME_ROOT_ENV_VARS:
        monkeypatch.setenv(variable, "unused")
    generated = configure_runtime_roots(tmp_path)
    with ArtifactCtl(
        {
            "blob_store": {"root_dir": str(generated.parent / "blobs")},
            "index": {"sqlite_path": str(generated.parent / "index.db")},
            "views": {"auto_generate": []},
        }
    ) as ctl:
        refs = [
            retain(ctl, {"session_id": session, "events": []}, evaluator="independent")
            for session in ("first", "second")
        ]
        assert refs[0] != refs[1]
        assert all(ctl.get(ref).agent_id == "independent" for ref in refs)
        assert len(ctl.index.active_reference_shas()) == 2


class TimeoutSession:
    visible_transcript = "timed out"
    closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True


class TimeoutProbe:
    session_id = "timeout"

    def __init__(self):
        self.pty = TimeoutSession()
        self.calls = 0

    def session(self):
        return self.pty

    def wait_ready(self, _session):
        pass

    def run_turn(self, _session, _scenario, **_kwargs):
        self.calls += 1
        raise TimeoutError("inflight provider timeout")

    def environment(self):
        return {}


def test_timeout_is_retained_and_closes_the_process_without_rescue(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "tests.e2e.skill_learning.live_evidence.read_focus_evidence",
        lambda *_: ([], [], ""),
    )
    probe = TimeoutProbe()
    _, result, elapsed = focus_turn(probe, "local task", tmp_path / "transcript.txt")
    assert result["completed"] is False and "timeout" in result["error"]
    assert probe.calls == 1 and probe.pty.closed and elapsed >= 0


@pytest.fixture
def campaign(tmp_path, monkeypatch):
    for variable in RUNTIME_ROOT_ENV_VARS:
        monkeypatch.setenv(variable, "unused")
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.test",
            "commit",
            "--allow-empty",
            "-qm",
            "test: empty fixture",
        ],
        cwd=source,
        check=True,
    )
    evaluation = tmp_path / "evaluation"
    (evaluation / "sealed").mkdir(parents=True)
    cases = ["first", "second", "third"]
    protocol = {
        "cases": [
            {"case_id": case, "applicable": index < 2, "phase": "heldout"}
            for index, case in enumerate(cases)
        ],
        "order": [
            [key.case_id, key.arm, key.repeat] for key in paired_trial_order(cases)
        ],
    }
    for name, field in (
        ("oracle.py", "oracle_sha256"),
        ("corpus.json", "corpus_sha256"),
    ):
        (evaluation / "sealed" / name).write_text("fixture")
        protocol[field] = sha256(b"fixture").hexdigest()
    write_json(evaluation / "protocol-freeze.json", protocol)
    config_path = tmp_path / "original.json"
    original = {"agents": {"fixture": {"name": "Fixture", "provider": "echo"}}}
    write_json(config_path, original)
    for suffix in ("MAX_TOTAL_LLM_TOKENS", "MAX_TOOL_CALLS", "MAX_ELAPSED_MS"):
        monkeypatch.setenv("OPENMINION_BRAIN_" + suffix, "unused")
    campaign = TransferCampaign(
        {
            "attempt_root": str(tmp_path / "attempt"),
            "source_root": str(source),
            "evaluation_root": str(evaluation),
            "config_path": str(config_path),
            "agent_id": "fixture",
            "evaluator_id": "independent",
            "provider": "echo",
            "model": "echo",
        }
    )
    try:
        yield campaign
    finally:
        campaign.ctl.close()


def test_fresh_arm_storage_and_loaded_caps(campaign, tmp_path):
    baseline = campaign.probe("baseline", tmp_path)
    candidate = campaign.probe("candidate", tmp_path)
    base_skill, candidate_skill = _skill_config(baseline), _skill_config(candidate)
    assert base_skill.sqlite_path != candidate_skill.sqlite_path
    assert base_skill.runtime_workflow_observation_enabled
    assert (
        baseline.environment()["OPENMINION_HOME"]
        != candidate.environment()["OPENMINION_HOME"]
    )
    policy = Policy(campaign.config["brain"]["tool_policy"])
    for name in (
        "exec.run",
        "file.list_dir",
        "file.read",
        "file.read_range",
        "file.find",
        "file.search",
        "file.write",
        "file.edit",
        "code.repo_map",
        "code.grep",
        "code.patch",
    ):
        policy.ensure_tool_allowed(name)
    for name in ("web.fetch", "file.trash", "delegate", "security.scan_web"):
        with pytest.raises(ToolRuntimeError, match="not allowed"):
            policy.ensure_tool_allowed(name)
    before = deepcopy(campaign.state)
    campaign.state["stage"] = "author_started"
    with pytest.raises(AssertionError, match="cannot be repeated"):
        campaign.author()
    assert campaign.state["slots"] == before["slots"] == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tokens", 40001),
        ("tokens", None),
        ("tool_calls", 41),
        ("elapsed_seconds", 300.1),
    ],
)
def test_exhausted_or_unknown_budget_prevents_another_turn(campaign, field, value):
    observation = {
        "session_id": "first",
        "trace_id": "trace-first",
        "trace_ref": "artifact:first",
        "command_ref": "artifact:command",
        "result_ref": "artifact:result",
        "outcome": "correct",
        "tokens": 100,
        "tool_calls": 1,
        "elapsed_seconds": 1,
    }
    observation[field] = value
    campaign.state["slots"] = [
        {"label": "first", "phase": None, "observation": observation}
    ]
    assert not campaign.has_budget()
    with pytest.raises(AssertionError, match="budget is unavailable"):
        campaign.turn("second", case="first")
    assert len(campaign.state["slots"]) == 1


def test_prepare_freezes_real_review_without_applying_candidate(campaign, tmp_path):
    probe = campaign.probe("acquisition-inspect", campaign.root, acquisition=True)
    skill = Skill(_skill_config(probe))
    try:
        for source in ("development-1", "development-2"):
            observed = observe_runtime_workflow(
                skill.store,
                agent_id=campaign.agent,
                source_run_ref=source,
                intent_category="modify",
                capability_category="code",
                tool_names=["exec.run"],
            )
        staged = stage_authored_skill_proposal(
            store=skill.store,
            agent_id=campaign.agent,
            shape_id=observed.shape_id,
            draft=SkillProposalDraft(
                name="fixture-procedure",
                short_description="Verify local repairs",
                risk_class="medium",
                tools=["exec.run"],
                procedure=SkillProcedure(
                    when_to_use=["Local repairs"],
                    do_not_use=["Remote systems"],
                    steps=["Read the documented contract before editing"],
                    validation=["Run regression tests"],
                ),
            ),
        )
        campaign.state.update(
            stage="awaiting_review",
            proposal=staged.proposal.model_dump(mode="json"),
            acquisition_evidence_refs=staged.proposal.evidence_refs,
        )
        review_path = tmp_path / "review.json"
        write_json(
            review_path,
            {
                "reviewer_id": "independent-reviewer",
                "review_policy_id": "fixture-review",
                "criterion_decisions": [
                    {
                        "criterion_id": "fit",
                        "status": "accepted",
                        "comment": "Fits observed development workflow",
                    }
                ],
            },
        )
        campaign.inputs["review_path"] = str(review_path)
        campaign.prepare()
        assert campaign.state["stage"] == "prepared"
        assert campaign.state["reviewer_id"] == "independent-reviewer"
        assert skill.catalog_summaries(campaign.agent) == []
        with ArtifactCtl({}) as reopened:
            manifest = MeasurementManifest.model_validate_json(
                reopened.read_bytes(campaign.state["manifest_ref"])
            )
            assert manifest.identity.proposal_id == staged.proposal.proposal_id
            assert (
                reopened.get(campaign.state["manifest_ref"]).agent_id
                == campaign.evaluator
            )
        with pytest.raises(AssertionError):
            campaign.prepare()
    finally:
        skill.close()
