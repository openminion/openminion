from __future__ import annotations

import json
from pathlib import Path

from openminion.cli.main import main


def _last_json(capsys) -> dict:
    out = capsys.readouterr().out.strip().splitlines()
    return json.loads("\n".join(out))


def test_project_learning_instruction_loop_e2e(tmp_path: Path, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    target = repo / "OPENMINION.md"
    target.write_text("# Repo\n", encoding="utf-8")
    store = tmp_path / "store.json"

    assert (
        main(
            [
                "--home-root",
                str(tmp_path),
                "project-learning",
                "--store",
                str(store),
                "stage-opportunity",
                "--opportunity-id",
                "opp-e2e",
                "--source-kind",
                "self_improvement_note",
                "--evidence-ref",
                "note:failure",
                "--observed-count",
                "2",
            ]
        )
        == 0
    )
    assert _last_json(capsys)["opportunity"]["opportunity_id"] == "opp-e2e"

    assert (
        main(
            [
                "--home-root",
                str(tmp_path),
                "project-learning",
                "--store",
                str(store),
                "stage-proposal",
                "--dir",
                str(repo),
                "--candidate-id",
                "cand-e2e",
                "--opportunity-id",
                "opp-e2e",
                "--proposal-kind",
                "append_bullet",
                "--summary",
                "Add validation habit",
                "--evidence-ref",
                "note:failure",
                "--author-source",
                "operator",
                "--text",
                "Run the focused validation before closeout.",
            ]
        )
        == 0
    )
    assert _last_json(capsys)["proposal"]["candidate_id"] == "cand-e2e"

    assert (
        main(
            [
                "--home-root",
                str(tmp_path),
                "project-learning",
                "--store",
                str(store),
                "reject",
                "cand-e2e",
            ]
        )
        == 0
    )
    assert _last_json(capsys)["proposal"]["state"] == "rejected"
    assert target.read_text(encoding="utf-8") == "# Repo\n"

    assert (
        main(
            [
                "--home-root",
                str(tmp_path),
                "project-learning",
                "--store",
                str(store),
                "stage-proposal",
                "--dir",
                str(repo),
                "--candidate-id",
                "cand-stale",
                "--proposal-kind",
                "append_bullet",
                "--summary",
                "Add validation habit",
                "--evidence-ref",
                "note:failure",
                "--author-source",
                "operator",
                "--text",
                "Run the focused validation before closeout.",
            ]
        )
        == 0
    )
    _last_json(capsys)

    assert (
        main(
            [
                "--home-root",
                str(tmp_path),
                "project-learning",
                "--store",
                str(store),
                "approve",
                "cand-stale",
                "--approval-id",
                "approval-e2e",
                "--actor-id",
                "operator",
                "--session-id",
                "session-e2e",
                "--yes",
            ]
        )
        == 0
    )
    assert _last_json(capsys)["approval"]["approval_id"] == "approval-e2e"

    target.write_text("# Repo\nchanged\n", encoding="utf-8")
    assert (
        main(
            [
                "--home-root",
                str(tmp_path),
                "project-learning",
                "--store",
                str(store),
                "apply",
                "approval-e2e",
            ]
        )
        == 0
    )
    assert _last_json(capsys)["proposal"]["state"] == "suppressed"
    target.write_text("# Repo\n", encoding="utf-8")
    assert (
        main(
            [
                "--home-root",
                str(tmp_path),
                "project-learning",
                "--store",
                str(store),
                "stage-proposal",
                "--dir",
                str(repo),
                "--candidate-id",
                "cand-apply",
                "--proposal-kind",
                "append_bullet",
                "--summary",
                "Add validation habit",
                "--evidence-ref",
                "note:failure",
                "--author-source",
                "operator",
                "--text",
                "Run the focused validation before closeout.",
            ]
        )
        == 0
    )
    _last_json(capsys)

    assert (
        main(
            [
                "--home-root",
                str(tmp_path),
                "project-learning",
                "--store",
                str(store),
                "approve",
                "cand-apply",
                "--approval-id",
                "approval-e2e-2",
                "--actor-id",
                "operator",
                "--session-id",
                "session-e2e",
                "--yes",
            ]
        )
        == 0
    )
    _last_json(capsys)
    assert (
        main(
            [
                "--home-root",
                str(tmp_path),
                "project-learning",
                "--store",
                str(store),
                "apply",
                "approval-e2e-2",
            ]
        )
        == 0
    )
    assert _last_json(capsys)["proposal"]["state"] == "promoted"
    assert "Run the focused validation" in target.read_text(encoding="utf-8")

    assert (
        main(
            [
                "--home-root",
                str(tmp_path),
                "project-learning",
                "--store",
                str(store),
                "rollback",
                "cand-apply",
            ]
        )
        == 0
    )
    assert _last_json(capsys)["proposal"]["state"] == "rolled_back"
    assert target.read_text(encoding="utf-8") == "# Repo\n"
