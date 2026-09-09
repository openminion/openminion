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
from openminion.modules.storage.runtime.sqlite import resolve_database_path
from openminion.modules.task import TaskManager, load_latest_project_checkpoint
from openminion.modules.task.constants import DEFAULT_INTEGRATED_SQLITE_SUBPATH


_FIXTURES = Path(__file__).parent / "fixtures"
_OACC_SOURCE = json.loads(
    (_FIXTURES / "convergence_turn_sources.json").read_text(encoding="utf-8")
)["scenarios"]
pytestmark = pytest.mark.e2e


def _plan_metadata(
    plan_id: str,
    *,
    revision: bool = False,
    complete: bool = False,
) -> dict[str, str]:
    if revision:
        metadata = {
            "task_plan.revision": json.dumps(
                {
                    "plan_id": plan_id,
                    "revision_id": f"{plan_id}-1",
                    "criterion_ids": ["verification:artifact"],
                    "verifier_refs": ["verification:cycle-1:failed"],
                    "revised_steps": [
                        {
                            "step_id": "deliver",
                            "description": "Complete and verify the required artifacts",
                            "status": "completed" if complete else "in_progress",
                        }
                    ],
                    "continue_plan_autonomously": True,
                }
            )
        }
        if complete:
            metadata["task_plan.completed"] = json.dumps({"plan_id": plan_id})
        return metadata
    return {
        "task_plan": json.dumps(
            {
                "plan_id": plan_id,
                "objective": "Produce the required verified artifacts",
                "criterion_ids": ["verification:artifact"],
                "status": "completed" if complete else "active",
                "steps": [
                    {
                        "step_id": "deliver",
                        "description": "Complete and verify the required artifacts",
                        "status": "completed" if complete else "in_progress",
                    }
                ],
                "continue_plan_autonomously": True,
            }
        )
    }


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
    return _run_cli(
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
    )["run"]


def _run_cli(args: list[str]) -> dict[str, object]:
    output = io.StringIO()
    with redirect_stdout(output):
        assert main(args) == 0
    return json.loads(output.getvalue())


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
                **_plan_metadata("research-conflict", complete=True),
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
                **_plan_metadata(
                    "research-to-code",
                    revision=turns > 1,
                    complete=turns > 1,
                ),
            },
        }

    run = _run_project(
        tmp_path,
        monkeypatch,
        domain="research",
        turn=turn,
        verify_command=(
            f"{shlex.quote(sys.executable)} -m pytest -q tests/test_strategy.py"
        ),
    )

    assert run["status"] == scenario["expected_terminal"]["run_status"]
    assert turns == 2


def test_cross_application_project_blocks_before_turn(
    tmp_path,
    monkeypatch,
) -> None:
    turn_called = False

    def turn(*, config_path, payload):  # noqa: ANN001, ARG001
        nonlocal turn_called
        turn_called = True
        return {"final_text": "unexpected"}

    run = _run_project(
        tmp_path,
        monkeypatch,
        domain="cross_application",
        turn=turn,
        verify_command=f"{shlex.quote(sys.executable)} -c 'raise SystemExit(0)'",
    )

    assert run["status"] == "blocked"
    assert run["last_error"]["code"] == "project_domain_not_configured"
    assert turn_called is False


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


def test_non_git_research_project_resumes_two_milestones(
    tmp_path: Path,
    monkeypatch,
) -> None:
    decision_path = tmp_path / "decision.json"
    implementation_path = tmp_path / "implementation.py"
    turns = 0

    def turn(*, config_path, payload):  # noqa: ANN001, ARG001
        nonlocal turns
        turns += 1
        if turns == 1:
            decision_path.write_text('{"choice":"bounded"}\n', encoding="utf-8")
            metadata = {
                "artifact_refs": ["file:decision.json"],
                "evidence_kinds": ["artifact"],
                "task_plan": json.dumps(
                    {
                        "plan_id": "research-delivery",
                        "objective": "Research and implement the bounded choice",
                        "criterion_ids": ["verification:artifacts"],
                        "steps": [
                            {
                                "step_id": "research",
                                "description": "Record the source-backed decision",
                                "status": "completed",
                            },
                            {
                                "step_id": "implement",
                                "description": "Implement the selected approach",
                                "depends_on": ["research"],
                            },
                        ],
                        "continue_plan_autonomously": True,
                    }
                ),
            }
        else:
            implementation_path.write_text("CHOICE = 'bounded'\n", encoding="utf-8")
            metadata = {
                "artifact_refs": ["file:implementation.py"],
                "evidence_kinds": ["artifact"],
                **_plan_metadata(
                    "research-delivery",
                    revision=True,
                    complete=True,
                ),
            }
            revision = json.loads(metadata["task_plan.revision"])
            revision["criterion_ids"] = ["verification:artifacts"]
            revision["revised_steps"][0]["step_id"] = "research"
            revision["revised_steps"].append(
                {
                    "step_id": "implement",
                    "description": "Implement the selected approach",
                    "status": "completed",
                    "depends_on": ["research"],
                }
            )
            metadata["task_plan.revision"] = json.dumps(revision)
        return {"final_text": f"milestone {turns}", "metadata": metadata}

    monkeypatch.setattr("openminion.cli.commands.autonomy_project.run_turn", turn)
    verify = " ".join(
        (
            shlex.quote(sys.executable),
            "-c",
            shlex.quote(
                "from pathlib import Path; "
                "raise SystemExit(0 if Path('decision.json').is_file() "
                "and Path('implementation.py').is_file() else 1)"
            ),
        )
    )
    base_args = [
        "--home-root",
        str(tmp_path / "home"),
        "--data-root",
        str(tmp_path / "data"),
        "--no-interactive",
        "autonomy",
    ]
    started = _run_cli(
        [
            *base_args,
            "start",
            "--goal",
            "Research and implement the bounded choice",
            "--workspace",
            str(tmp_path),
            "--verification-domain",
            "research",
            "--max-iterations",
            "1",
            "--verify-command",
            verify,
            "--json",
        ]
    )["run"]
    completed = _run_cli(
        [
            *base_args,
            "resume",
            str(started["run_id"]),
            "--max-iterations",
            "2",
            "--json",
        ]
    )["run"]

    manager = TaskManager.for_lifecycle_db(
        db_path=resolve_database_path(
            DEFAULT_INTEGRATED_SQLITE_SUBPATH,
            env={
                "OPENMINION_HOME": str(tmp_path / "home"),
                "OPENMINION_DATA_ROOT": str(tmp_path / "data"),
            },
        )
    )
    checkpoint = load_latest_project_checkpoint(
        manager,
        task_id=str(completed["task_id"]),
    )

    assert started["status"] == "blocked"
    assert completed["status"] == "completed"
    assert turns == 2
    assert not (tmp_path / ".git").exists()
    assert checkpoint is not None
    assert checkpoint.payload["task_plan"]["status"] == "completed"
    assert checkpoint.project_run.committed_cycle_count == 2
