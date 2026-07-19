from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

from openminion.cli.commands.project_learning import run_project_learning


def _args(command: str, store: Path, **kwargs) -> Namespace:
    payload = {
        "project_learning_command": command,
        "store": str(store),
        "home_root": None,
        "data_root": None,
    }
    payload.update(kwargs)
    return Namespace(**payload)


def test_project_learning_cli_stages_and_lists_proposal(
    tmp_path: Path,
    capsys,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "OPENMINION.md").write_text("# Repo\n", encoding="utf-8")
    store = tmp_path / "store.json"

    status = run_project_learning(
        _args(
            "stage-proposal",
            store,
            dir=str(repo),
            candidate_id="cand-cli",
            opportunity_id="opp-1",
            target_name=None,
            proposal_kind="append_section",
            summary="Add closeout section",
            evidence_ref=["trace:1"],
            author_source="operator",
            text="## Closeout\nRun focused validation.",
            text_file=None,
            suggested_patch="",
            risk_level="low",
            validation_hint="pytest focused",
        )
    )
    assert status == 0
    staged = json.loads(capsys.readouterr().out)
    assert staged["proposal"]["candidate_id"] == "cand-cli"

    status = run_project_learning(_args("list", store))

    assert status == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["count"] == 1


def test_project_learning_cli_refuses_untrusted_approval(
    tmp_path: Path, capsys
) -> None:
    store = tmp_path / "store.json"

    status = run_project_learning(
        _args(
            "approve",
            store,
            candidate_id="missing",
            approval_id=None,
            actor_id="operator",
            session_id="session",
            yes=False,
        )
    )

    assert status == 2
    payload = json.loads(capsys.readouterr().err)
    assert payload["ok"] is False
    assert payload["error"] == "trusted_approval_required"


def test_project_learning_cli_renders_author_handoff(tmp_path: Path, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "OPENMINION.md").write_text("# Repo\n", encoding="utf-8")
    store = tmp_path / "store.json"
    run_project_learning(
        _args(
            "stage-opportunity",
            store,
            opportunity_id="opp-cli",
            source_kind="self_improvement_note",
            evidence_ref=["note:1"],
            observed_count=2,
            target_hint="OPENMINION.md",
        )
    )
    capsys.readouterr()

    status = run_project_learning(
        _args(
            "author-handoff",
            store,
            opportunity_id="opp-cli",
            dir=str(repo),
            target_name=None,
        )
    )

    assert status == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["handoff"]["opportunity"]["opportunity_id"] == "opp-cli"
    assert (
        payload["handoff"]["authoring_contract"][
            "runtime_may_not_infer_instruction_text"
        ]
        is True
    )
