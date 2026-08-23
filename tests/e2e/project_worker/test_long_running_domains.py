from __future__ import annotations

import io
import json
import shlex
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from openminion.cli.main import main


_FIXTURES = Path(__file__).parent / "fixtures"
_OACC_SOURCE = json.loads(
    (_FIXTURES / "convergence_turn_sources.json").read_text(encoding="utf-8")
)["scenarios"]
pytestmark = pytest.mark.e2e


def _run_project(
    tmp_path: Path,
    monkeypatch,
    *,
    domain: str,
    turn,
    verify_command: str,
    max_iterations: int = 2,
) -> dict[str, object]:
    monkeypatch.setattr("openminion.cli.commands.autonomy_project.run_turn", turn)
    output = io.StringIO()
    with redirect_stdout(output):
        code = main(
            [
                "--home-root",
                str(tmp_path / "home"),
                "--data-root",
                str(tmp_path / "data"),
                "--no-interactive",
                "autonomy",
                "start",
                "--goal",
                f"Produce the verified {domain} artifact",
                "--workspace",
                str(tmp_path),
                "--verification-domain",
                domain,
                "--max-iterations",
                str(max_iterations),
                "--verify-command",
                verify_command,
                "--json",
            ]
        )
    assert code == 0
    return json.loads(output.getvalue())["run"]


def test_frozen_research_project_preserves_sources_and_conflict(
    tmp_path, monkeypatch
) -> None:
    scenario = _OACC_SOURCE["research_conflict"]
    manifest_path = tmp_path / "research_manifest.json"
    manifest_path.write_bytes((_FIXTURES / "research_manifest.json").read_bytes())
    report_path = tmp_path / "research_report.json"

    def turn(*, config_path, payload):  # noqa: ANN001, ARG001
        source_turn = scenario["turns"][0]
        effect = source_turn["fixture_turn_effects"][0]
        assert effect["operation"] == "write_exact"
        assert effect["path"] == "research_report.json"
        report_path.write_text(effect["content"], encoding="utf-8")
        return {
            "final_text": source_turn["summary"],
            "metadata": {
                "artifact_refs": source_turn["evidence_refs"],
                "evidence_kinds": source_turn["evidence_kinds"],
                "effect_refs": source_turn["effect_refs"],
            },
        }

    verify = " ".join(
        (
            shlex.quote(sys.executable),
            shlex.quote(str(_FIXTURES / "verify_research.py")),
            shlex.quote(str(manifest_path)),
            shlex.quote(str(report_path)),
        )
    )
    run = _run_project(
        tmp_path,
        monkeypatch,
        domain="research",
        turn=turn,
        verify_command=verify,
        max_iterations=scenario["budgets"]["cycles"],
    )

    assert run["status"] == "completed"
    assert report_path.exists()


def test_oacc_research_to_code_requires_both_artifacts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    scenario = _OACC_SOURCE["research_to_code"]
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    for relative, content in scenario["seed_files"].items():
        (tmp_path / relative).write_text(content, encoding="utf-8")

    code_only = tmp_path / "code-only"
    (code_only / "src").mkdir(parents=True)
    (code_only / "tests").mkdir()
    for relative, content in scenario["seed_files"].items():
        (code_only / relative).write_text(content, encoding="utf-8")
    code_effect = scenario["turns"][1]["fixture_turn_effects"][0]
    (code_only / code_effect["path"]).write_text(
        code_effect["content"],
        encoding="utf-8",
    )
    code_only_result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tests/test_strategy.py"],
        cwd=code_only,
        capture_output=True,
        text=True,
        check=False,
    )
    assert code_only_result.returncode != 0

    turns = 0

    def turn(*, config_path, payload):  # noqa: ANN001, ARG001
        nonlocal turns
        source_turn = scenario["turns"][turns]
        turns += 1
        effect = source_turn["fixture_turn_effects"][0]
        assert effect["operation"] == "write_exact"
        (tmp_path / effect["path"]).write_text(effect["content"], encoding="utf-8")
        return {
            "final_text": source_turn["summary"],
            "metadata": {
                "artifact_refs": source_turn["evidence_refs"],
                "evidence_kinds": source_turn["evidence_kinds"],
                "effect_refs": source_turn["effect_refs"],
            },
        }

    run = _run_project(
        tmp_path,
        monkeypatch,
        domain="cross_application",
        turn=turn,
        verify_command=(
            f"{shlex.quote(sys.executable)} -m pytest -q tests/test_strategy.py"
        ),
    )

    assert run["status"] == scenario["expected_terminal"]["run_status"]
    assert turns == 2


def test_cross_application_project_uses_same_worker_and_typed_verifier(
    tmp_path,
    monkeypatch,
) -> None:
    handoff_path = tmp_path / "handoff.json"

    def turn(*, config_path, payload):  # noqa: ANN001, ARG001
        handoff_path.write_text(
            json.dumps(
                {
                    "schema_version": "project-handoff.v1",
                    "title": "Release handoff",
                    "owners": ["engineering", "operations"],
                    "items": [
                        {
                            "name": "tests",
                            "status": "ready",
                            "source_ref": "proof:tests",
                        },
                        {
                            "name": "deployment",
                            "status": "blocked",
                            "source_ref": "decision:approval",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        return {
            "final_text": "handoff written",
            "metadata": {
                "artifact_refs": [f"file:{handoff_path.name}"],
                "evidence_kinds": ["artifact"],
            },
        }

    verify = " ".join(
        (
            shlex.quote(sys.executable),
            shlex.quote(str(_FIXTURES / "verify_handoff.py")),
            shlex.quote(str(handoff_path)),
        )
    )
    run = _run_project(
        tmp_path,
        monkeypatch,
        domain="cross_application",
        turn=turn,
        verify_command=verify,
    )

    assert run["status"] == "completed"
    assert handoff_path.exists()


def test_research_verifier_rejects_invented_source(tmp_path) -> None:
    manifest_path = tmp_path / "research_manifest.json"
    manifest_path.write_bytes((_FIXTURES / "research_manifest.json").read_bytes())
    report_path = tmp_path / "research_report.json"
    report_path.write_text(
        json.dumps(
            {
                "as_of_date": "2026-08-15",
                "source_ledger": [{"source_id": "invented"}],
                "claim_ledger": [{}],
                "unavailable_source_ids": [],
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(_FIXTURES / "verify_research.py"),
            str(manifest_path),
            str(report_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
