from __future__ import annotations

import io
import json
import shlex
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from openminion.cli.main import main
from openminion.cli.parser.base import build_parser
from openminion.modules.task import (
    TaskManager,
    build_project_run_projection,
    build_autonomy_run,
    record_project_cycle,
    ProjectCycleDecision,
)


def _run_cli(args: list[str]) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        try:
            code = main(args)
        except SystemExit as exc:
            code = int(exc.code) if exc.code is not None else 0
    return code, buf.getvalue()


def _root_args(tmp_path: Path) -> list[str]:
    home = tmp_path / "home"
    data = tmp_path / "data"
    return ["--home-root", str(home), "--data-root", str(data), "--no-interactive"]


def _run_project_cli(
    tmp_path: Path,
    db_path: Path,
    command: str,
    *args: str,
    task_id: str = "task-1",
) -> tuple[int, str]:
    return _run_cli(
        [
            *_root_args(tmp_path),
            "autonomy",
            "project",
            "--task-db",
            str(db_path),
            command,
            task_id,
            *args,
            "--json",
        ]
    )


def test_autonomy_parser_registers_list_show_start_resume_cancel() -> None:
    parser = build_parser()

    list_args = parser.parse_args(["autonomy", "list", "--json"])
    show_args = parser.parse_args(["autonomy", "show", "awrk_1", "--json"])
    start_args = parser.parse_args(["autonomy", "start", "--goal", "ship"])
    resume_args = parser.parse_args(["autonomy", "resume", "awrk_1"])
    cancel_args = parser.parse_args(["autonomy", "cancel", "awrk_1"])
    project_args = parser.parse_args(
        [
            "autonomy",
            "project",
            "--task-db",
            "/tmp/tasks.db",
            "reprioritize",
            "task-1",
            "--priority",
            "high",
        ]
    )

    assert list_args.autonomy_command == "list"
    assert show_args.autonomy_command == "show"
    assert start_args.autonomy_command == "start"
    assert resume_args.autonomy_command == "resume"
    assert cancel_args.autonomy_command == "cancel"
    assert project_args.autonomy_command == "project"
    assert project_args.project_command == "reprioritize"
    assert callable(list_args.handler)


def _seed_project_task(db_path: Path) -> None:
    manager = TaskManager.for_lifecycle_db(db_path=db_path)
    manager.create_task(
        session_id="session-1",
        mode_name="project",
        goal="ship project controls",
        agent_id="agent-1",
        task_id="task-1",
    )
    autonomy_run = build_autonomy_run(
        goal_text="ship project controls",
        goal_id="goal-1",
        session_id="session-1",
        workspace_ref="local:/workspace#commit=abc;dirty=clean",
        max_iterations=3,
    ).model_copy(update={"task_id": "task-1"})
    project_run = build_project_run_projection(
        autonomy_run,
        objective_ledger_ref="artifact:objective.json",
        evidence_ledger_ref="artifact:evidence.jsonl",
        resume_packet_ref="artifact:resume.json",
        operator_decision_log_ref="artifact:operator-decisions.jsonl",
        capability_plan_ref="artifact:capabilities.json",
        metrics_summary_ref="artifact:metrics.json",
    )
    record_project_cycle(
        manager,
        project_run,
        cycle_id="cycle-1",
        milestone="control proof",
        intended_action="exercise operator controls",
        evidence_refs=("artifact:evidence.jsonl#cycle-1",),
        validation_refs=("pytest:tests/cli/test_autonomy_command.py",),
        decision=ProjectCycleDecision.CONTINUE,
    )


def test_autonomy_project_operator_controls_use_task_lifecycle_db(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "tasks.db"
    _seed_project_task(db_path)

    pause_code, pause_output = _run_project_cli(tmp_path, db_path, "pause")
    resume_code, _resume_output = _run_project_cli(tmp_path, db_path, "resume")
    priority_code, _priority_output = _run_project_cli(
        tmp_path,
        db_path,
        "reprioritize",
        "--priority",
        "high",
    )
    answer_code, _answer_output = _run_project_cli(
        tmp_path,
        db_path,
        "answer-input-request",
        "--input-request-id",
        "input-1",
        "--answer",
        "continue",
    )
    budget_code, budget_output = _run_project_cli(
        tmp_path,
        db_path,
        "extend-budget",
        "--extra-iterations",
        "2",
        "--extra-tool-calls",
        "5",
    )
    report_code, report_output = _run_project_cli(tmp_path, db_path, "report")

    paused = json.loads(pause_output)["project"]
    budget = json.loads(budget_output)["project"]
    report = json.loads(report_output)["project_report"]

    assert pause_code == 0
    assert resume_code == 0
    assert priority_code == 0
    assert answer_code == 0
    assert budget_code == 0
    assert report_code == 0
    assert paused["state"] == "paused"
    assert budget["state"] == "active"
    assert budget["priority"] == "high"
    assert budget["operator_answer_count"] == 1
    assert budget["budget_extensions"]["extra_iterations"] == 2
    assert budget["budget_extensions"]["extra_tool_calls"] == 5
    assert budget["cycle_count"] == 1
    assert report["project_run"]["task_id"] == "task-1"
    assert report["outcome"] == "in_progress"
    assert report["metrics"]["operator_intervention_count"] == 3
    assert report["metrics"]["proof_packet_completeness_percent"] == 100.0


def test_autonomy_start_replay_writes_terminal_proof(tmp_path: Path) -> None:
    code, output = _run_cli(
        [
            *_root_args(tmp_path),
            "autonomy",
            "start",
            "--goal",
            "ship the proof packet",
            "--replay-response",
            "completed from replay",
            "--json",
        ]
    )

    payload = json.loads(output)
    run = payload["run"]

    assert code == 0
    assert run["status"] == "completed"
    assert run["phase"] == "closed"
    proof_path = Path(run["proof_packet_ref"])
    assert proof_path.exists()
    proof = json.loads(proof_path.read_text(encoding="utf-8"))
    assert proof["final_operator_summary"] == "completed from replay"
    assert proof["commands_run"][0]["status"] == "succeeded"
    assert proof["workspace_ref"].startswith("local:")


def test_autonomy_start_with_verify_command_records_test_evidence(
    tmp_path: Path,
) -> None:
    verify_command = f"{shlex.quote(sys.executable)} -c 'print(\"verify ok\")'"

    code, output = _run_cli(
        [
            *_root_args(tmp_path),
            "autonomy",
            "start",
            "--goal",
            "ship with verification",
            "--replay-response",
            "completed from replay",
            "--verify-command",
            verify_command,
            "--json",
        ]
    )

    run = json.loads(output)["run"]
    proof = json.loads(Path(run["proof_packet_ref"]).read_text(encoding="utf-8"))

    assert code == 0
    assert run["status"] == "completed"
    assert proof["validation_summary"].endswith("verification commands passed.")
    assert proof["tests_run"][0]["status"] == "passed"
    assert proof["tests_run"][0]["summary"] == "verify ok"


def test_autonomy_start_blocks_when_verify_command_fails(tmp_path: Path) -> None:
    verify_command = f"{shlex.quote(sys.executable)} -c 'raise SystemExit(7)'"

    code, output = _run_cli(
        [
            *_root_args(tmp_path),
            "autonomy",
            "start",
            "--goal",
            "ship with failing verification",
            "--replay-response",
            "completed from replay",
            "--verify-command",
            verify_command,
            "--json",
        ]
    )

    run = json.loads(output)["run"]
    proof = json.loads(Path(run["proof_packet_ref"]).read_text(encoding="utf-8"))

    assert code == 0
    assert run["status"] == "blocked"
    assert run["last_error"]["code"] == "VERIFICATION_FAILED"
    assert proof["failure_or_blocker"]["code"] == "VERIFICATION_FAILED"
    assert proof["tests_run"][0]["status"] == "failed"
    assert proof["tests_run"][0]["exit_code"] == 7


def test_autonomy_start_require_verification_blocks_without_check(
    tmp_path: Path,
) -> None:
    code, output = _run_cli(
        [
            *_root_args(tmp_path),
            "autonomy",
            "start",
            "--goal",
            "must verify",
            "--replay-response",
            "completed from replay",
            "--require-verification",
            "--json",
        ]
    )

    run = json.loads(output)["run"]
    proof = json.loads(Path(run["proof_packet_ref"]).read_text(encoding="utf-8"))

    assert code == 0
    assert run["status"] == "blocked"
    assert run["last_error"]["code"] == "VERIFICATION_REQUIRED"
    assert proof["failure_or_blocker"]["code"] == "VERIFICATION_REQUIRED"
    assert proof["tests_run"] == []


def test_autonomy_start_verification_waiver_records_failed_check(
    tmp_path: Path,
) -> None:
    verify_command = f"{shlex.quote(sys.executable)} -c 'raise SystemExit(9)'"

    code, output = _run_cli(
        [
            *_root_args(tmp_path),
            "autonomy",
            "start",
            "--goal",
            "waived verification",
            "--replay-response",
            "completed from replay",
            "--verify-command",
            verify_command,
            "--verification-waiver",
            "operator accepted failing fixture for local proof",
            "--json",
        ]
    )

    run = json.loads(output)["run"]
    proof = json.loads(Path(run["proof_packet_ref"]).read_text(encoding="utf-8"))

    assert code == 0
    assert run["status"] == "completed"
    assert proof["tests_run"][0]["status"] == "failed"
    assert proof["verification_waiver"]["reason"] == (
        "operator accepted failing fixture for local proof"
    )
    assert proof["failure_or_blocker"] is None


def test_autonomy_list_and_show_use_same_store(tmp_path: Path) -> None:
    start_code, start_output = _run_cli(
        [
            *_root_args(tmp_path),
            "autonomy",
            "start",
            "--goal",
            "inspect me",
            "--replay-response",
            "done",
            "--json",
        ]
    )
    run_id = json.loads(start_output)["run"]["run_id"]

    list_code, list_output = _run_cli(
        [*_root_args(tmp_path), "autonomy", "list", "--json"]
    )
    show_code, show_output = _run_cli(
        [*_root_args(tmp_path), "autonomy", "show", run_id, "--json"]
    )

    assert start_code == 0
    assert list_code == 0
    assert show_code == 0
    assert json.loads(list_output)["runs"][0]["run_id"] == run_id
    assert json.loads(show_output)["run"]["run_id"] == run_id


def test_autonomy_show_can_include_terminal_proof(tmp_path: Path) -> None:
    _code, output = _run_cli(
        [
            *_root_args(tmp_path),
            "autonomy",
            "start",
            "--goal",
            "inspect proof",
            "--replay-response",
            "done",
            "--json",
        ]
    )
    run_id = json.loads(output)["run"]["run_id"]

    code, show_output = _run_cli(
        [
            *_root_args(tmp_path),
            "autonomy",
            "show",
            run_id,
            "--include-proof",
            "--json",
        ]
    )

    payload = json.loads(show_output)
    assert code == 0
    assert payload["run"]["run_id"] == run_id
    assert payload["proof"]["run_id"] == run_id
    assert payload["proof"]["status"] == "completed"


def test_autonomy_start_records_delegated_role_evidence(tmp_path: Path) -> None:
    code, output = _run_cli(
        [
            *_root_args(tmp_path),
            "autonomy",
            "start",
            "--goal",
            "synthesize delegated proof",
            "--replay-response",
            "base summary",
            "--delegate-result",
            "worker:success:patched implementation",
            "--delegate-result",
            "explorer:success:checked owner surfaces",
            "--delegate-result",
            "reviewer:success:reviewed verification evidence",
            "--json",
        ]
    )

    run = json.loads(output)["run"]
    proof = json.loads(Path(run["proof_packet_ref"]).read_text(encoding="utf-8"))

    assert code == 0
    assert run["status"] == "completed"
    assert "Delegation evidence:" in run["operator_summary"]
    assert [item["role"] for item in proof["delegation_results"]] == [
        "worker",
        "explorer",
        "reviewer",
    ]
    assert proof["delegation_aggregation"]["total_children"] == 3
    assert proof["delegation_aggregation"]["success_count"] == 3


def test_autonomy_start_records_context_budget_evidence(tmp_path: Path) -> None:
    long_goal = " ".join(["preserve context while trimming older details"] * 80)

    code, output = _run_cli(
        [
            *_root_args(tmp_path),
            "autonomy",
            "start",
            "--goal",
            long_goal,
            "--replay-response",
            "done",
            "--delegate-result",
            "worker:success:" + "worker detail " * 80,
            "--context-budget-tokens",
            "40",
            "--context-required-fact",
            "must keep sqlite migration note",
            "--json",
        ]
    )

    run = json.loads(output)["run"]
    proof = json.loads(Path(run["proof_packet_ref"]).read_text(encoding="utf-8"))
    budget = proof["context_budget"]

    assert code == 0
    assert run["status"] == "completed"
    assert budget["max_tokens"] == 40
    assert budget["estimated_tokens_after"] < budget["estimated_tokens_before"]
    assert budget["retained_required_facts"] == ["must keep sqlite migration note"]


def test_autonomy_start_with_zero_iterations_blocks_with_proof(
    tmp_path: Path,
) -> None:
    code, output = _run_cli(
        [
            *_root_args(tmp_path),
            "autonomy",
            "start",
            "--goal",
            "blocked goal",
            "--max-iterations",
            "0",
            "--json",
        ]
    )

    run = json.loads(output)["run"]
    proof = json.loads(Path(run["proof_packet_ref"]).read_text(encoding="utf-8"))

    assert code == 0
    assert run["status"] == "blocked"
    assert proof["failure_or_blocker"]["code"] == "BUDGET_EXHAUSTED"
    assert "Resume with --max-iterations" in run["next_action_hint"]


def test_autonomy_resume_blocked_run_completes_with_replay(tmp_path: Path) -> None:
    _code, output = _run_cli(
        [
            *_root_args(tmp_path),
            "autonomy",
            "start",
            "--goal",
            "resume me",
            "--max-iterations",
            "0",
            "--json",
        ]
    )
    run_id = json.loads(output)["run"]["run_id"]

    code, resume_output = _run_cli(
        [
            *_root_args(tmp_path),
            "autonomy",
            "resume",
            run_id,
            "--replay-response",
            "resumed successfully",
            "--json",
        ]
    )

    run = json.loads(resume_output)["run"]
    assert code == 0
    assert run["status"] == "completed"
    assert run["operator_summary"] == "resumed successfully"


def test_autonomy_cancel_writes_cancelled_proof(tmp_path: Path) -> None:
    _code, output = _run_cli(
        [
            *_root_args(tmp_path),
            "autonomy",
            "start",
            "--goal",
            "cancel me",
            "--max-iterations",
            "0",
            "--json",
        ]
    )
    run_id = json.loads(output)["run"]["run_id"]

    code, cancel_output = _run_cli(
        [*_root_args(tmp_path), "autonomy", "cancel", run_id, "--json"]
    )

    run = json.loads(cancel_output)["run"]
    proof = json.loads(Path(run["proof_packet_ref"]).read_text(encoding="utf-8"))
    assert code == 0
    assert run["status"] == "cancelled"
    assert proof["status"] == "cancelled"


def test_autonomy_start_requires_goal(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main([*_root_args(tmp_path), "autonomy", "start"])

    assert exc_info.value.code == 2
