from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from openminion.modules.skill.cli import main
from openminion.modules.skill.proposal import SkillProposal, SkillProposalDraft
from openminion.modules.skill.proposal.queue import create_proposal
from openminion.modules.skill.runtime.skill import Skill


def _config_path(tmp_path: Path) -> Path:
    db = tmp_path / "skill.db"
    cfg = tmp_path / "skill.json"
    cfg.write_text(
        json.dumps(
            {
                "skill": {
                    "sqlite_path": str(db),
                    "blob_root": str(tmp_path / "blob"),
                    "fallback_root": str(tmp_path / "fallback"),
                    "wal": False,
                }
            }
        ),
        encoding="utf-8",
    )
    return cfg


def _run_cli(argv: list[str]) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(argv)
    assert rc == 0, f"CLI exit code: {rc}; stdout={buf.getvalue()!r}"
    return buf.getvalue()


def _run_cli_expect_failure(argv: list[str]) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        with pytest.raises(SystemExit) as excinfo:
            main(argv)
    rc = (
        int(excinfo.value.code)
        if isinstance(excinfo.value.code, int)
        else 1
        if excinfo.value.code
        else 0
    )
    return rc, buf.getvalue()


def _seed_proposal(tmp_path: Path, *, proposal_id: str = "sprq-cli-1") -> str:
    cfg = _config_path(tmp_path)
    ctl = Skill(str(cfg))
    try:
        create_proposal(
            ctl.store,
            SkillProposal(
                proposal_id=proposal_id,
                source_task_shape_ref="task_shape:research|live_information|latest_news",
                proposed_skill_definition=SkillProposalDraft(
                    name="research-latest-news-playbook",
                    display_name="Research Latest News Playbook",
                    short_description="Persisted via SPRQ for CLI test.",
                    tools=[],
                    tags=["research", "live_information", "latest_news"],
                    risk_class="low",
                    applies_to={"intents": ["latest_news"], "steps": []},
                    inputs_schema=[],
                    verification_rules=[],
                ),
                evidence_refs=["performance:research|live_information|latest_news"],
                proposer_policy_id="skill_promotion_cadence_v1",
                proposed_at="",
            ),
        )
    finally:
        ctl.close()
    return proposal_id


def test_cli_proposal_list_returns_pending_by_default(tmp_path: Path) -> None:
    _seed_proposal(tmp_path)
    cfg = _config_path(tmp_path)
    out = _run_cli(["--config", str(cfg), "proposal-list"])
    payload = json.loads(out)
    assert payload["ok"] is True
    proposals = payload["proposals"]
    assert len(proposals) == 1
    assert proposals[0]["proposal_id"] == "sprq-cli-1"
    assert proposals[0]["queue_state"] == "pending"


def test_cli_proposal_list_all_returns_all_states(tmp_path: Path) -> None:
    _seed_proposal(tmp_path, proposal_id="sprq-cli-2")
    cfg = _config_path(tmp_path)
    out = _run_cli(["--config", str(cfg), "proposal-list", "--queue-state", "all"])
    payload = json.loads(out)
    assert payload["ok"] is True
    assert {row["proposal_id"] for row in payload["proposals"]} == {"sprq-cli-2"}


def test_cli_proposal_inspect_returns_full_record(tmp_path: Path) -> None:
    _seed_proposal(tmp_path)
    cfg = _config_path(tmp_path)
    out = _run_cli(["--config", str(cfg), "proposal-inspect", "sprq-cli-1"])
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["proposal"]["proposal_id"] == "sprq-cli-1"
    assert payload["proposal"]["queue_state"] == "pending"
    assert payload["proposal"]["review"] is None


def test_cli_proposal_inspect_returns_not_found(tmp_path: Path) -> None:
    cfg = _config_path(tmp_path)
    rc, out = _run_cli_expect_failure(
        ["--config", str(cfg), "proposal-inspect", "no-such-id"]
    )
    assert rc == 1
    payload = json.loads(out)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "NOT_FOUND"


def test_cli_proposal_review_persists_and_transitions(tmp_path: Path) -> None:
    _seed_proposal(tmp_path)
    cfg = _config_path(tmp_path)
    out = _run_cli(
        [
            "--config",
            str(cfg),
            "proposal-review",
            "sprq-cli-1",
            "--reviewer-id",
            "operator-cli",
            "--review-policy-id",
            "sprq_review_v1",
            "--criterion",
            "fit:accepted:matches recurring intent",
        ]
    )
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["review"]["status"] == "accepted"
    assert payload["review"]["reviewer_id"] == "operator-cli"

    # Inspect now reports reviewed state.
    inspect_out = _run_cli(["--config", str(cfg), "proposal-inspect", "sprq-cli-1"])
    inspect_payload = json.loads(inspect_out)
    assert inspect_payload["proposal"]["queue_state"] == "reviewed"
    assert inspect_payload["proposal"]["review_status"] == "accepted"


@pytest.mark.parametrize(
    "runtime_id", ["runtime", "system", "auto", "automatic", "self"]
)
def test_cli_proposal_review_rejects_runtime_reviewer(
    tmp_path: Path, runtime_id: str
) -> None:
    _seed_proposal(tmp_path)
    cfg = _config_path(tmp_path)
    rc, out = _run_cli_expect_failure(
        [
            "--config",
            str(cfg),
            "proposal-review",
            "sprq-cli-1",
            "--reviewer-id",
            runtime_id,
            "--criterion",
            "fit:accepted:should never persist",
        ]
    )
    assert rc == 1
    payload = json.loads(out)
    assert payload["ok"] is False
    # Proposal still pending.
    inspect_out = _run_cli(["--config", str(cfg), "proposal-inspect", "sprq-cli-1"])
    inspect_payload = json.loads(inspect_out)
    assert inspect_payload["proposal"]["queue_state"] == "pending"


def test_cli_proposal_review_refuses_missing_criteria(tmp_path: Path) -> None:
    _seed_proposal(tmp_path)
    cfg = _config_path(tmp_path)
    rc, out = _run_cli_expect_failure(
        [
            "--config",
            str(cfg),
            "proposal-review",
            "sprq-cli-1",
            "--reviewer-id",
            "operator-cli",
            "--criterion",
            "bad-format-no-colons",
        ]
    )
    assert rc == 1
    payload = json.loads(out)
    assert payload["ok"] is False


def test_cli_proposal_apply_emits_addition(tmp_path: Path) -> None:
    _seed_proposal(tmp_path)
    cfg = _config_path(tmp_path)
    _run_cli(
        [
            "--config",
            str(cfg),
            "proposal-review",
            "sprq-cli-1",
            "--reviewer-id",
            "operator-cli",
            "--criterion",
            "fit:accepted:matches recurring intent",
        ]
    )
    apply_out = _run_cli(["--config", str(cfg), "proposal-apply", "sprq-cli-1"])
    apply_payload = json.loads(apply_out)
    assert apply_payload["ok"] is True
    addition = apply_payload["addition"]
    assert addition["added_skill_id"].startswith("emergent.")
    assert addition["added_by"] == "operator-cli"


def test_cli_proposal_apply_refuses_pending(tmp_path: Path) -> None:
    _seed_proposal(tmp_path)
    cfg = _config_path(tmp_path)
    rc, out = _run_cli_expect_failure(
        ["--config", str(cfg), "proposal-apply", "sprq-cli-1"]
    )
    assert rc == 1
    payload = json.loads(out)
    assert payload["ok"] is False
