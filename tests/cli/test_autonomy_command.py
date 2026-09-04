from __future__ import annotations

import io
import json
import shlex
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from openminion.cli.main import main
from openminion.cli.commands.autonomy_project import run_project_turn
from openminion.cli.parser.base import build_parser
from openminion.modules.task import (
    AutonomyRunError,
    TaskManager,
    build_project_run_projection,
    build_autonomy_run,
    record_project_cycle,
    ProjectCycleDecision,
)
from openminion.modules.task.project import AutonomyLoopConditionKind
from openminion.modules.llm.providers.contracts import ProviderError
from openminion.services.runtime.project_worker import (
    ProjectTurnRequest,
    ProjectTurnResult,
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


def test_project_turn_uses_canonical_successful_tool_results_as_progress(
    monkeypatch,
) -> None:
    run = build_autonomy_run(
        goal_text="finish the project",
        goal_id="goal-1",
        session_id="session-1",
        workspace_ref="local:/workspace#commit=abc;dirty=clean",
        max_iterations=3,
    )
    captured_payloads: list[dict[str, object]] = []

    def run_turn(**kwargs):  # noqa: ANN003
        captured_payloads.append(kwargs["payload"])
        return {
            "body": "worked",
            "metadata": {
                "run_id": "gateway-run-1",
                "task_plan": json.dumps(
                    {
                        "plan_id": "plan-1",
                        "objective": "finish the project",
                        "criterion_ids": ["criterion-tests"],
                        "steps": [{"step_id": "build", "description": "Build it"}],
                    }
                ),
                "task_plan.revision": json.dumps(
                    {
                        "plan_id": "plan-1",
                        "revision_id": "revision-1",
                        "verifier_refs": ["verify:failed-1"],
                        "revised_steps": [
                            {"step_id": "build", "description": "Repair it"}
                        ],
                    }
                ),
                "tool_calls_cumulative": json.dumps(
                    [
                        {"tool_name": "file.write", "ok": True, "call_id": "write-1"},
                        {"tool_name": "exec.run", "ok": False, "call_id": "test-1"},
                    ]
                ),
            },
        }

    monkeypatch.setattr("openminion.cli.commands.autonomy_project.run_turn", run_turn)

    result = run_project_turn(
        run,
        ProjectTurnRequest(
            run_id=run.run_id,
            project_run_id="project-1",
            task_id="task-1",
            goal_id="goal-1",
            session_id="session-1",
            cycle_id="cycle-1",
            milestone="finish the project",
            prompt="work",
        ),
    )

    assert result.evidence_refs == ("tool-call:write-1",)
    assert result.evidence_kinds == ("tool_result",)
    assert result.tool_call_count == 2
    assert result.gateway_run_id == "gateway-run-1"
    assert result.task_plan is not None
    assert result.task_plan.criterion_ids == ["criterion-tests"]
    assert result.task_plan_revision is not None
    assert result.task_plan_revision.revision_id == "revision-1"
    assert captured_payloads[0]["inbound_metadata"]["workspace_root"] == "/workspace"
    assert captured_payloads[0]["inbound_metadata"]["caller_handles_delivery"] == (
        "true"
    )
    assert captured_payloads[0]["inbound_metadata"]["conversation_id"] == "project-1"
    assert captured_payloads[0]["inbound_metadata"]["resume"] == "true"
    assert captured_payloads[0]["timeout_seconds"] == 300
    assert captured_payloads[0]["inbound_metadata"]["turn_timeout_seconds"] == "300"
    assert json.loads(
        captured_payloads[0]["inbound_metadata"]["permission_overrides"]
    ) == {
        "file.copy": "bypass",
        "file.move": "bypass",
        "file.write": "bypass",
    }


def test_project_turn_maps_waiting_brain_status_to_project_condition(
    monkeypatch,
) -> None:
    run = build_autonomy_run(
        goal_text="finish the project",
        goal_id="goal-1",
        session_id="session-1",
        workspace_ref="local:/workspace#commit=abc;dirty=clean",
        max_iterations=3,
    )
    monkeypatch.setattr(
        "openminion.cli.commands.autonomy_project.run_turn",
        lambda **_kwargs: {
            "body": "Approval is required before writing files.",
            "metadata": {"brain_status": "waiting_user"},
        },
    )

    result = run_project_turn(
        run,
        ProjectTurnRequest(
            run_id=run.run_id,
            project_run_id="project-1",
            task_id="task-1",
            goal_id="goal-1",
            session_id="session-1",
            cycle_id="cycle-1",
            milestone="finish the project",
            prompt="work",
        ),
    )

    assert result.condition == AutonomyLoopConditionKind.WAITING


def test_project_turn_preserves_cli_error_payload(monkeypatch) -> None:
    run = build_autonomy_run(
        goal_text="finish the project",
        goal_id="goal-1",
        session_id="session-1",
        workspace_ref="local:/workspace#commit=abc;dirty=clean",
        max_iterations=3,
    )
    monkeypatch.setattr(
        "openminion.cli.commands.autonomy_project.run_turn",
        lambda **_kwargs: {
            "error": {"code": "context_overflow", "message": "budget exceeded"},
            "body": "budget exceeded",
            "metadata": {"project_condition": "retryable_failure"},
        },
    )

    result = run_project_turn(
        run,
        ProjectTurnRequest(
            run_id=run.run_id,
            project_run_id="project-1",
            task_id="task-1",
            goal_id="goal-1",
            session_id="session-1",
            cycle_id="cycle-1",
            milestone="finish the project",
            prompt="work",
        ),
    )

    assert result.error is not None
    assert result.error.code == "context_overflow"
    assert result.error.message == "budget exceeded"
    assert result.condition == AutonomyLoopConditionKind.RETRYABLE_FAILURE


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
    paused_payload = json.loads(pause_output)
    budget = json.loads(budget_output)["project"]
    budget_payload = json.loads(budget_output)
    report = json.loads(report_output)["project_report"]

    assert pause_code == 0
    assert resume_code == 0
    assert priority_code == 0
    assert answer_code == 0
    assert budget_code == 0
    assert report_code == 0
    assert paused["state"] == "paused"
    assert paused_payload["operator_inbox"]["state"] == "waiting"
    assert paused_payload["operator_inbox"]["resume_action"] == "continue"
    assert paused_payload["operator_inbox"]["last_checkpoint_id"].endswith(":cycle-1")
    assert budget["state"] == "active"
    assert budget_payload["operator_inbox"]["state"] == "running"
    assert budget_payload["operator_inbox"]["resume_action"] == "continue"
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
            "--verification-waiver",
            "replay fixture has no external verifier",
            "--turn-timeout-seconds",
            "600",
            "--verification-timeout-seconds",
            "900",
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
    assert run["execution_selectors"]["turn_timeout_seconds"] == 600
    assert run["execution_selectors"]["verification_timeout_seconds"] == 900


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
    assert proof["validation_summary"].endswith("verification commands did not pass.")


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
            "--verification-waiver",
            "replay fixture has no external verifier",
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
            "--verification-waiver",
            "replay fixture has no external verifier",
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
            "--verification-waiver",
            "replay fixture has no external verifier",
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
            "--verification-waiver",
            "replay fixture has no external verifier",
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
            "--max-iterations",
            "1",
            "--verification-waiver",
            "replay fixture has no external verifier",
            "--json",
        ]
    )

    run = json.loads(resume_output)["run"]
    assert code == 0
    assert run["status"] == "completed"
    assert run["operator_summary"] == "resumed successfully"


def test_autonomy_resume_preserves_cycle_summaries(tmp_path: Path) -> None:
    verify_command = f"{shlex.quote(sys.executable)} -c 'raise SystemExit(1)'"
    _code, output = _run_cli(
        [
            *_root_args(tmp_path),
            "autonomy",
            "start",
            "--goal",
            "summarize every cycle",
            "--replay-response",
            "first cycle",
            "--verify-command",
            verify_command,
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
            "second cycle",
            "--max-iterations",
            "2",
            "--verification-waiver",
            "local cumulative-summary fixture",
            "--json",
        ]
    )

    run = json.loads(resume_output)["run"]
    proof = json.loads(Path(run["proof_packet_ref"]).read_text())
    assert code == 0
    assert run["status"] == "completed"
    assert proof["cycle_summaries"] == ["first cycle", "second cycle"]


def test_autonomy_resume_preflight_preserves_cycle_summaries(
    tmp_path: Path,
    monkeypatch,
) -> None:
    verify_command = f"{shlex.quote(sys.executable)} -c 'raise SystemExit(1)'"
    _code, output = _run_cli(
        [
            *_root_args(tmp_path),
            "autonomy",
            "start",
            "--goal",
            "preserve summary through blocked resume",
            "--replay-response",
            "first cycle",
            "--verify-command",
            verify_command,
            "--json",
        ]
    )
    run_id = json.loads(output)["run"]["run_id"]
    monkeypatch.setattr(
        "openminion.cli.commands.autonomy.verifier_preflight_error",
        lambda *_args, **_kwargs: AutonomyRunError(
            code="VERIFIER_UNAVAILABLE",
            message="verifier unavailable",
        ),
    )

    code, resume_output = _run_cli(
        [*_root_args(tmp_path), "autonomy", "resume", run_id, "--json"]
    )

    run = json.loads(resume_output)["run"]
    proof = json.loads(Path(run["proof_packet_ref"]).read_text(encoding="utf-8"))
    assert code == 0
    assert run["status"] == "blocked"
    assert proof["cycle_summaries"] == ["first cycle"]


def test_autonomy_resume_preserves_provider_error_after_blocked_checkpoint(
    tmp_path: Path,
    monkeypatch,
) -> None:
    verify_command = f"{shlex.quote(sys.executable)} -c 'raise SystemExit(1)'"
    _code, output = _run_cli(
        [
            *_root_args(tmp_path),
            "autonomy",
            "start",
            "--goal",
            "resume after provider interruption",
            "--replay-response",
            "first cycle",
            "--verify-command",
            verify_command,
            "--json",
        ]
    )
    run_id = json.loads(output)["run"]["run_id"]

    def fail_turn(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise ProviderError(
            "empty after retries",
            code="EMPTY_PROVIDER_RESPONSE",
        )

    monkeypatch.setattr(
        "openminion.cli.commands.autonomy.run_project_turn",
        fail_turn,
    )
    code, resume_output = _run_cli(
        [
            *_root_args(tmp_path),
            "autonomy",
            "resume",
            run_id,
            "--max-iterations",
            "2",
            "--json",
        ]
    )

    resumed = json.loads(resume_output)["run"]
    assert code == 0
    assert resumed["status"] == "failed"
    assert resumed["last_error"] == {
        "code": "EMPTY_PROVIDER_RESPONSE",
        "detail": None,
        "message": "empty after retries",
    }


def test_autonomy_start_interrupts_to_resumable_blocked_run(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def interrupt_turn(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise KeyboardInterrupt

    monkeypatch.setattr(
        "openminion.cli.commands.autonomy.run_project_turn",
        interrupt_turn,
    )
    code, output = _run_cli(
        [
            *_root_args(tmp_path),
            "autonomy",
            "start",
            "--goal",
            "resume after operator interruption",
            "--verification-waiver",
            "local interruption fixture",
            "--json",
        ]
    )

    run = json.loads(output)["run"]
    manager = TaskManager.for_lifecycle_db(db_path=tmp_path / "data/task/task.db")
    task = manager.get_task(run["task_id"])

    assert code == 130
    assert run["status"] == "blocked"
    assert run["phase"] == "closed"
    assert run["last_error"]["code"] == "OPERATOR_INTERRUPTED"
    assert run["next_action_hint"].endswith(f"autonomy resume {run['run_id']}`.")
    assert task is not None
    assert task.state.value == "paused"
    released_claim = manager.lifecycle_repository.acquire_project_cycle_claim(
        task_id=task.task_id,
        owner_id="replacement-worker",
        expected_checkpoint_id=run["checkpoint_id"],
    )
    manager.lifecycle_repository.release_project_cycle_claim(released_claim)

    monkeypatch.setattr(
        "openminion.cli.commands.autonomy.run_project_turn",
        lambda *_args, **_kwargs: ProjectTurnResult(summary="resumed successfully"),
    )
    resume_code, resume_output = _run_cli(
        [
            *_root_args(tmp_path),
            "autonomy",
            "resume",
            run["run_id"],
            "--verification-waiver",
            "local interruption fixture",
            "--json",
        ]
    )

    resumed = json.loads(resume_output)["run"]
    assert resume_code == 0
    assert resumed["run_id"] == run["run_id"]
    assert resumed["status"] == "completed"


def test_autonomy_resume_interrupts_to_resumable_blocked_run(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _code, output = _run_cli(
        [
            *_root_args(tmp_path),
            "autonomy",
            "start",
            "--goal",
            "interrupt a resumed run",
            "--max-iterations",
            "0",
            "--json",
        ]
    )
    run_id = json.loads(output)["run"]["run_id"]

    def interrupt_turn(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise KeyboardInterrupt

    monkeypatch.setattr(
        "openminion.cli.commands.autonomy.run_project_turn",
        interrupt_turn,
    )
    code, resume_output = _run_cli(
        [
            *_root_args(tmp_path),
            "autonomy",
            "resume",
            run_id,
            "--max-iterations",
            "1",
            "--verification-waiver",
            "local interruption fixture",
            "--json",
        ]
    )

    resumed = json.loads(resume_output)["run"]
    assert code == 130
    assert resumed["run_id"] == run_id
    assert resumed["status"] == "blocked"
    assert resumed["last_error"]["code"] == "OPERATOR_INTERRUPTED"


def test_autonomy_interrupt_after_completion_preserves_terminal_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def interrupt_proof(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise KeyboardInterrupt

    monkeypatch.setattr(
        "openminion.cli.commands.autonomy._write_terminal_proof",
        interrupt_proof,
    )
    code, output = _run_cli(
        [
            *_root_args(tmp_path),
            "autonomy",
            "start",
            "--goal",
            "preserve completed state",
            "--replay-response",
            "completed before interruption",
            "--verification-waiver",
            "local terminal fixture",
            "--json",
        ]
    )

    run = json.loads(output)["run"]
    assert code == 130
    assert run["status"] == "completed"
    assert run["operator_summary"] == "completed before interruption"
    assert run["last_error"] is None


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


def test_autonomy_cancel_preserves_cycle_summaries(tmp_path: Path) -> None:
    verify_command = f"{shlex.quote(sys.executable)} -c 'raise SystemExit(1)'"
    _code, output = _run_cli(
        [
            *_root_args(tmp_path),
            "autonomy",
            "start",
            "--goal",
            "cancel after one cycle",
            "--replay-response",
            "first cycle",
            "--verify-command",
            verify_command,
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
    assert proof["cycle_summaries"] == ["first cycle"]


def test_unattended_autonomy_schedules_one_cycle_and_cancel_removes_it(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class CronStore:
        def __init__(self) -> None:
            self.jobs: dict[str, dict[str, object]] = {}

        def add_cron_job(self, *, job_id, **kwargs):  # noqa: ANN001, ANN003
            self.jobs[job_id] = kwargs
            return job_id

        def delete_cron_job(self, job_id):  # noqa: ANN001
            self.jobs.pop(job_id, None)

        def close(self) -> None:
            return None

    cron_store = CronStore()
    monkeypatch.setattr(
        "openminion.cli.commands.autonomy_project.configured_cron_store",
        lambda _args, config_ref: cron_store,
    )
    monkeypatch.setattr(
        "openminion.cli.commands.autonomy.configured_cron_store",
        lambda _args, config_ref: cron_store,
    )
    verify_command = f"{shlex.quote(sys.executable)} -c 'raise SystemExit(0)'"
    code, output = _run_cli(
        [
            *_root_args(tmp_path),
            "autonomy",
            "start",
            "--goal",
            "run in the daemon",
            "--workspace",
            str(tmp_path),
            "--verify-command",
            verify_command,
            "--unattended",
            "--cycle-interval-seconds",
            "17",
            "--json",
        ]
    )
    run = json.loads(output)["run"]
    job_id = next(iter(cron_store.jobs))

    assert code == 0
    assert run["status"] == "running"
    assert cron_store.jobs[job_id]["payload"]["kind"] == "projectCycle"
    assert cron_store.jobs[job_id]["payload"]["cycle_interval_seconds"] == 17

    cancel_code, cancelled_output = _run_cli(
        [
            *_root_args(tmp_path),
            "autonomy",
            "cancel",
            run["run_id"],
            "--json",
        ]
    )

    assert cancel_code == 0
    assert json.loads(cancelled_output)["run"]["status"] == "cancelled"
    assert cron_store.jobs == {}


def test_autonomy_start_requires_goal(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main([*_root_args(tmp_path), "autonomy", "start"])

    assert exc_info.value.code == 2
