from __future__ import annotations

import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from openminion.cli.commands.autonomy_project import (
    build_project_launch_request,
    launch_project,
    parse_focus_project_launch,
)
from openminion.modules.brain.adapters.tool import ToolAdapter
from openminion.modules.brain.adapters.tool.project_git import git_push_action_scope
from openminion.modules.brain.execution.child_tasks import SubtaskSpec
from openminion.modules.brain.execution.worktree_children import (
    allocate_child_worktree,
    finalize_child_worktree,
    load_child_worktree_record,
)
from openminion.modules.brain.loop.strategies.coding.contracts import (
    PROJECT_RELEASE_ADDITIONAL_TOOLS,
)
from openminion.modules.brain.schemas import BudgetCounters, WorkingState
from openminion.modules.task import (
    AutonomyRunStatus,
    AutonomyRunStore,
    ProjectCycleDecision,
    TaskLifecycleState,
    TaskManager,
    TestEvidence as _TestEvidence,
    TestEvidenceStatus as _TestEvidenceStatus,
    load_latest_project_checkpoint,
)
from openminion.modules.task.autonomy import now_ms
from openminion.modules.task.plan import (
    TaskPlan,
    TaskPlanStepCompleted,
    TaskPlanTerminalSignal,
)
from openminion.modules.task.project.effects import (
    ProjectEffectRecord,
    ProjectEffectStatus,
    save_project_effect_record,
)
from openminion.modules.task.project.policy import issue_project_permission_grant
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.registry import ToolRegistry
from openminion.services.runtime.a2a_delegate import A2aRuntimeDelegateAdapter
from openminion.services.runtime.project_worker import (
    ProjectTurnRequest,
    ProjectTurnResult,
    ProjectWorker,
)
from openminion.tools.agent.plugin import _h_task_delegate
from openminion.tools.git import register as register_git
from openminion.tools.github import register as register_github
from openminion.tools.github.constants import DEFAULT_GITHUB_PROVIDER_ID
from openminion.tools.github.providers import (
    provider_registry,
    register_provider,
)
from tests.artifact.utils import artifact_ctl

pytestmark = pytest.mark.e2e
_GIT = shutil.which("git")


def _git(repo: Path, *args: str) -> str:
    assert _GIT is not None
    result = subprocess.run(
        [_GIT, "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _repository_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    boundary = tmp_path / "workspace"
    repository = boundary / "repository"
    remote = tmp_path / "remote.git"
    repository.mkdir(parents=True)
    remote.mkdir()
    _git(remote, "init", "-q", "--bare", "-b", "main")
    _git(repository, "init", "-q", "-b", "main")
    _git(repository, "config", "user.email", "orel@example.invalid")
    _git(repository, "config", "user.name", "OREL Fixture")
    _git(repository, "config", "commit.gpgsign", "false")
    (repository / "feature.py").write_text("VALUE = 0\n", encoding="utf-8")
    _git(repository, "add", "feature.py")
    _git(repository, "commit", "-q", "-m", "seed fixture")
    _git(repository, "remote", "add", "origin", str(remote))
    return boundary, repository, remote


def _passing_verification(repository: Path) -> tuple[_TestEvidence, ...]:
    timestamp = now_ms()
    return (
        _TestEvidence(
            command=("verify",),
            cwd_ref=str(repository),
            started_at_ms=timestamp,
            ended_at_ms=timestamp,
            exit_code=0,
            passed=1,
            failed=0,
            status=_TestEvidenceStatus.PASSED,
            summary="repository verifier passed",
        ),
    )


def _child_artifact(
    repository: Path,
    artifact_owner: Any,
    *,
    subtask_id: str,
    value: int,
    verifier_ref: str,
) -> dict[str, Any]:
    state = WorkingState(
        session_id="orel-session",
        agent_id="project-agent",
        goal="Deliver the repository change",
        budgets_remaining=BudgetCounters(
            ticks=4,
            tool_calls=4,
            a2a_calls=4,
            tokens=4_000,
            time_ms=60_000,
        ),
        trace_id=f"trace-{subtask_id}",
    )
    subtask = SubtaskSpec(
        subtask_id=subtask_id,
        goal="Implement the reviewed change",
        suggested_mode="act",
        inputs={"code_bearing": True, "workspace_root": str(repository)},
    )
    lease = allocate_child_worktree(subtask=subtask, child_state=state)
    assert lease is not None
    (lease.worktree / "feature.py").write_text(
        f"VALUE = {value}\n", encoding="utf-8"
    )
    context = SimpleNamespace(
        state=state,
        _services=SimpleNamespace(runner=SimpleNamespace(artifactctl=artifact_owner)),
    )
    record = finalize_child_worktree(
        context,
        lease=lease,
        status="done",
        validation={"passed": True, "verifier_refs": [verifier_ref]},
    )
    assert record is not None
    return record


def _git_adapter(repository: Path, manager: TaskManager) -> ToolAdapter:
    registry = ToolRegistry()
    register_git(registry)
    return ToolAdapter(
        workspace_root=repository,
        runtime_registry=registry,
        policy={
            "workspace_root": str(repository),
            "tools": {"allow_exact": ["git.add", "git.commit", "git.push"]},
        },
        task_manager=manager,
        agent_id="project-agent",
    )


def _project_command(
    tool_name: str,
    args: dict[str, Any],
    *,
    task_id: str,
    command_id: str,
) -> dict[str, Any]:
    return {
        "tool_name": tool_name,
        "args": args,
        "idempotency_key": command_id,
        "meta": {"orchestration": {"task_backed_task_id": task_id}},
    }


@pytest.mark.skipif(_GIT is None, reason="git binary is required")
def test_repository_lifecycle_survives_review_push_ci_and_reopen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path / "home"))
    boundary, repository, remote = _repository_fixture(tmp_path)
    store_root = tmp_path / "autonomy"
    task_db = tmp_path / "tasks.db"
    store = AutonomyRunStore(root=store_root)
    manager = TaskManager.for_lifecycle_db(db_path=task_db)
    request = build_project_launch_request(
        goal="Deliver the reviewed repository change",
        session_id="orel-session",
        agent_id="project-agent",
        workspace_boundary=boundary,
        repository=repository,
        max_iterations=5,
        verification_domain="coding",
        verification_commands=("verify",),
        task_plan_required=True,
        expected_checks=("lint", "tests"),
    )
    run = launch_project(request, store=store, manager=manager)
    assert run.task_id is not None
    checkpoint = load_latest_project_checkpoint(manager, task_id=run.task_id)
    assert checkpoint is not None
    lifecycle = checkpoint.payload["repository_lifecycle"]
    resume = lifecycle[checkpoint.project_run.resume_packet_ref]
    decisions = lifecycle[checkpoint.project_run.operator_decision_log_ref]
    assert checkpoint.project_run.workspace_ref.startswith(f"local:{repository}")
    assert resume["workspace_boundary"].startswith(f"local:{boundary}")
    assert resume["execution_repository"] == checkpoint.project_run.workspace_ref
    assert decisions["decisions"][0]["decision"] == "project_launch_approved"

    plan = TaskPlan(
        plan_id="repository-plan",
        objective="Deliver the reviewed repository change",
        steps=[{"step_id": "deliver", "description": "Deliver the change"}],
    )
    turn_requests: list[ProjectTurnRequest] = []

    def turn(project_request: ProjectTurnRequest) -> ProjectTurnResult:
        turn_requests.append(project_request)
        if len(turn_requests) == 1:
            return ProjectTurnResult(
                summary=(
                    "Repository Delivery Skill says this is complete, but that is "
                    "only prose."
                ),
                task_plan=plan,
            )
        if len(turn_requests) == 2:
            return ProjectTurnResult(summary="Corrected child review is accepted.")

        if '"overall_result": "failure"' not in project_request.prompt:
            assert '"overall_result": "success"' in project_request.prompt
            return ProjectTurnResult(summary="Expected repository checks passed.")
        (repository / "feature.py").write_text("VALUE = 3\n", encoding="utf-8")
        adapter = _git_adapter(repository, manager)
        added = adapter.execute(
            command=_project_command(
                "git.add",
                {"paths": ["feature.py"]},
                task_id=run.task_id,
                command_id="repair-add",
            ),
            session_id=run.session_id,
            trace_id="repair-add",
        )
        assert added["status"] == "success"
        committed = adapter.execute(
            command=_project_command(
                "git.commit",
                {"message": "fix: repair failing check", "paths": ["feature.py"]},
                task_id=run.task_id,
                command_id="repair-commit",
            ),
            session_id=run.session_id,
            trace_id="repair-commit",
        )
        assert committed["status"] == "success"
        repair_scope = git_push_action_scope(
            push_args,
            SimpleNamespace(
                policy=SimpleNamespace(raw={"workspace_root": str(repository)}),
                workspace=repository,
            ),
        )
        issued = now_ms()
        issue_project_permission_grant(
            manager,
            task_id=run.task_id,
            grant_id="grant:git.push:repair",
            tool_name="git.push",
            scope=repair_scope,
            issued_at_ms=issued,
            expires_at_ms=issued + 60_000,
            max_uses=1,
        )
        repaired = adapter.execute(
            command=_project_command(
                "git.push",
                push_args,
                task_id=run.task_id,
                command_id="repair-push",
            ),
            session_id=run.session_id,
            trace_id="repair-push",
        )
        assert repaired["status"] == "success"
        repaired_heads.append(repaired["outputs"]["parsed"]["remote_oid"])
        return ProjectTurnResult(
            summary="Repaired the failed check.",
            task_plan_step_completed=TaskPlanStepCompleted(
                plan_id=plan.plan_id,
                step_id="deliver",
                output_summary="Corrected, reviewed, committed, and pushed",
            ),
            task_plan_completed=TaskPlanTerminalSignal(plan_id=plan.plan_id),
        )

    first_cycle = ProjectWorker(
        task_manager=manager,
        autonomy_store=store,
        turn=turn,
        verify=lambda: _passing_verification(repository),
        owner_id="worker-before-review",
    ).run_cycle(run.run_id)
    assert first_cycle.decision == ProjectCycleDecision.CONTINUE
    assert first_cycle.run.status == AutonomyRunStatus.RUNNING
    assert set(turn_requests[0].allowed_tools).isdisjoint(
        PROJECT_RELEASE_ADDITIONAL_TOOLS
    )
    planned = load_latest_project_checkpoint(manager, task_id=run.task_id)
    assert planned is not None
    assert planned.payload["verification_closure"]["status"] == "verified"
    assert planned.payload["task_plan"]["status"] != "completed"

    artifact_root = tmp_path / ".openminion"
    corrected_alias = ""
    with artifact_ctl(artifact_root) as ctl:
        records: dict[str, dict[str, Any]] = {}

        def delegated_call(*, command, session_id, trace_id):
            del session_id, trace_id
            target = command["target_agent_id"]
            if target == "implementer":
                record = _child_artifact(
                    repository,
                    ctl,
                    subtask_id="initial-child",
                    value=1,
                    verifier_ref="verify:initial-child",
                )
                records[target] = record
                return {
                    "status": "success",
                    "summary": "initial child complete",
                    "outputs": {
                        "child_artifact": {
                            "record_alias": record["record_alias"],
                            "target_digest": record["target_digest"],
                        }
                    },
                }
            if target == "corrector":
                record = _child_artifact(
                    repository,
                    ctl,
                    subtask_id="corrected-child",
                    value=2,
                    verifier_ref="verify:corrected-child",
                )
                records[target] = record
                return {
                    "status": "success",
                    "summary": "corrected child complete",
                    "outputs": {
                        "child_artifact": {
                            "record_alias": record["record_alias"],
                            "target_digest": record["target_digest"],
                        }
                    },
                }
            record = records["corrector" if target == "final-reviewer" else "implementer"]
            passed = target == "final-reviewer"
            return {
                "status": "success",
                "summary": "review complete",
                "outputs": {
                    "child_agent_id": target,
                    "findings": []
                    if passed
                    else [
                        {
                            "priority": "P1",
                            "owner": "feature implementation",
                            "message": "Set the reviewed value to 2.",
                        }
                    ],
                    "passed": passed,
                    "target_digest": record["target_digest"],
                    "verifier_refs": record["validation"]["verifier_refs"],
                },
            }

        seam = A2aRuntimeDelegateAdapter(
            a2a_call=delegated_call,
            parent_agent_id="project-agent",
        )
        delegate_context = SimpleNamespace(
            a2a_delegate_api=seam,
            artifactctl=ctl,
            policy=SimpleNamespace(raw={}),
            workspace=repository,
            session_id=run.session_id,
        )
        initial = _h_task_delegate(
            {
                "mode": "sync",
                "agent_id": "implementer",
                "instruction": "Implement the approved repository change.",
            },
            delegate_context,
        )["outputs"]["child_artifact"]
        stale = seam.review_readonly(
            reviewer_agent_id="initial-reviewer",
            objective="Review the initial child.",
            criteria=["no blocking findings"],
            readable_base_repository=str(repository),
            bundle_ref=records["implementer"]["artifact"]["bundle_ref"],
            target_digest="stale-digest",
            diff=records["implementer"]["diff"],
            verifier_refs=records["implementer"]["validation"]["verifier_refs"],
            repository_instructions="AGENTS.md",
            timeout_seconds=30,
        )
        assert stale.ok is False and stale.error_code == "A2A_REVIEW_TARGET_MISMATCH"
        review = _h_task_delegate(
            {
                "mode": "review",
                "agent_id": "initial-reviewer",
                "instruction": "Review the initial child.",
                "review_criteria": ["no blocking findings"],
                "repository_instructions": "AGENTS.md",
                "child_artifact": initial,
            },
            delegate_context,
        )
        assert review["status"] == "correction_required"
        with pytest.raises(ToolRuntimeError) as unapproved:
            _h_task_delegate(
                {"mode": "accept", "child_artifact": initial}, delegate_context
            )
        assert unapproved.value.details["reason_code"] == "review_failed"
        rejected = _h_task_delegate(
            {"mode": "reject", "child_artifact": initial}, delegate_context
        )
        assert rejected["status"] == "rejected"
        correction = _h_task_delegate(
            {
                "mode": "sync",
                "agent_id": "corrector",
                "instruction": (
                    "Set the reviewed value to 2. Replace child digest "
                    f"{records['implementer']['target_digest']}."
                ),
            },
            delegate_context,
        )["outputs"]["child_artifact"]
        assert correction["target_digest"] != initial["target_digest"]
        final_review = _h_task_delegate(
            {
                "mode": "review",
                "agent_id": "final-reviewer",
                "instruction": "Review the corrected child.",
                "review_criteria": ["no blocking findings"],
                "repository_instructions": "AGENTS.md",
                "child_artifact": correction,
            },
            delegate_context,
        )
        assert final_review["status"] == "passed"
        corrected_alias = correction["record_alias"]

    manager.close()
    manager = TaskManager.for_lifecycle_db(db_path=task_db)
    with artifact_ctl(artifact_root) as ctl:
        accepted = _h_task_delegate(
            {
                "mode": "accept",
                "child_artifact": {"record_alias": corrected_alias},
            },
            SimpleNamespace(artifactctl=ctl, session_id=run.session_id),
        )
        assert accepted["status"] == "accepted"
        assert load_child_worktree_record(ctl, corrected_alias)[
            "integration_status"
        ] == "accepted"
    assert (repository / "feature.py").read_text(encoding="utf-8") == "VALUE = 2\n"

    adapter = _git_adapter(repository, manager)
    committed = adapter.execute(
        command=_project_command(
            "git.commit",
            {"message": "feat: apply reviewed child", "paths": ["feature.py"]},
            task_id=run.task_id,
            command_id="reviewed-commit",
        ),
        session_id=run.session_id,
        trace_id="reviewed-commit",
    )
    assert committed["status"] == "success"
    push_args = {
        "remote": "origin",
        "source_ref": "refs/heads/main",
        "target_ref": "refs/heads/main",
    }
    push_command = _project_command(
        "git.push", push_args, task_id=run.task_id, command_id="reviewed-push"
    )
    denied = adapter.execute(
        command=push_command,
        session_id=run.session_id,
        trace_id="push-denied",
    )
    assert denied["error"]["code"] == "POLICY_DENIED"
    assert not (remote / "refs" / "heads" / "main").exists()
    scope = git_push_action_scope(
        push_args,
        SimpleNamespace(
            policy=SimpleNamespace(raw={"workspace_root": str(repository)}),
            workspace=repository,
        ),
    )
    issued = now_ms()
    issue_project_permission_grant(
        manager,
        task_id=run.task_id,
        grant_id="grant:git.push:reviewed",
        tool_name="git.push",
        scope=scope,
        issued_at_ms=issued,
        expires_at_ms=issued + 60_000,
        max_uses=1,
    )
    from openminion.tools.git import remote as git_remote

    original_run_git = git_remote.run_git
    push_calls = 0

    def count_push(args, **kwargs):
        nonlocal push_calls
        if args[0] == "push":
            push_calls += 1
        return original_run_git(args, **kwargs)

    monkeypatch.setattr(git_remote, "run_git", count_push)
    pushed = adapter.execute(
        command=push_command,
        session_id=run.session_id,
        trace_id="push-approved",
    )
    replayed = adapter.execute(
        command=push_command,
        session_id=run.session_id,
        trace_id="push-replayed",
    )
    assert pushed["status"] == replayed["status"] == "success"
    assert replayed["outputs"]["parsed"]["reconciled"] is True
    assert push_calls == 1
    first_head = pushed["outputs"]["parsed"]["remote_oid"]
    save_project_effect_record(
        manager,
        ProjectEffectRecord(
            effect_id="effect:github.open_pr:fixture",
            task_id=run.task_id,
            idempotency_key="open-pr-fixture",
            actor_ref="agent:project-agent",
            capability_ref="github.open_pr",
            precondition_refs=(f"git:head:{first_head}",),
            result_ref="github:pull:openminion/fixture#17",
            non_reversible_reason="The fixture pull request remains open.",
            status=ProjectEffectStatus.SUCCEEDED,
        ),
        receipt={
            "owner": "openminion",
            "repo": "fixture",
            "number": 17,
            "head": "main",
            "base": "dev",
            "head_sha": first_head,
        },
    )

    repaired_heads: list[str] = []
    check_results = iter(
        (
            (first_head, "pending", ["tests"], []),
            (
                first_head,
                "failure",
                [],
                [
                    {
                        "name": "tests",
                        "conclusion": "failure",
                        "url": "https://github.invalid/check/1",
                        "output_title": "tests failed",
                        "output_summary": "one failure",
                        "output_text": "VALUE must be 3",
                        "expected": True,
                    }
                ],
            ),
            (None, "pending", ["lint"], []),
            (None, "success", [], []),
        )
    )

    def fetch_checks(args: object) -> dict[str, object]:
        head, result, missing, failures = next(check_results)
        expected_head = head or repaired_heads[-1]
        assert args == {
            "owner": "openminion",
            "repo": "fixture",
            "head_sha": expected_head,
            "expected_checks": ["lint", "tests"],
        }
        return {
            "head_sha": expected_head,
            "overall_result": result,
            "expected_checks": ["lint", "tests"],
            "missing_expected_checks": missing,
            "failure_facts": failures,
        }

    worker = ProjectWorker(
        task_manager=manager,
        autonomy_store=store,
        turn=turn,
        verify=lambda: _passing_verification(repository),
        owner_id="worker-after-review",
        fetch_checks=fetch_checks,
    )
    opened = worker.run_cycle(run.run_id)
    pending_head1 = worker.run_cycle(
        run.run_id,
        triggering_cron_job_id=opened.project_run.next_wake_job_id,
    )
    repaired = worker.run_cycle(
        run.run_id,
        triggering_cron_job_id=pending_head1.project_run.next_wake_job_id,
    )
    pending_head2 = worker.run_cycle(
        run.run_id,
        triggering_cron_job_id=repaired.project_run.next_wake_job_id,
    )
    completed = worker.run_cycle(
        run.run_id,
        triggering_cron_job_id=pending_head2.project_run.next_wake_job_id,
    )
    assert opened.check_events[-1]["overall_result"] == "pending"
    assert repaired.check_events[0]["overall_result"] == "failure"
    assert repaired_heads and repaired_heads[0] != first_head
    assert completed.check_events[-1]["overall_result"] == "success"
    assert completed.decision == ProjectCycleDecision.STOP
    assert completed.run.status == AutonomyRunStatus.COMPLETED

    manager.close()
    reopened_manager = TaskManager.for_lifecycle_db(db_path=task_db)
    reopened_store = AutonomyRunStore(root=store_root)
    reopened = load_latest_project_checkpoint(
        reopened_manager, task_id=run.task_id
    )
    assert reopened is not None
    observation = reopened.payload["repository_lifecycle"][
        reopened.project_run.resume_packet_ref
    ]["ci_observation"]
    assert reopened_store.require(run.run_id).status == AutonomyRunStatus.COMPLETED
    assert reopened_manager.get_task(run.task_id).state == TaskLifecycleState.DONE
    assert reopened.project_run.project_run_id == checkpoint.project_run.project_run_id
    assert reopened.project_run.task_id == run.task_id
    assert reopened.project_run.verification_state.value == "verified"
    assert reopened.payload["task_plan"]["status"] == "completed"
    assert observation["head_sha"] == repaired_heads[0]
    assert observation["overall_result"] == "success"
    assert len(reopened.project_run.effect_refs) == len(
        set(reopened.project_run.effect_refs)
    )
    evidence = reopened.payload["repository_lifecycle"][
        reopened.project_run.evidence_ledger_ref
    ]["receipts"]
    assert any(ref.startswith("effect:git.push:") for ref in evidence)
    assert any(ref.startswith("verification:") for ref in evidence)


@pytest.mark.skipif(_GIT is None, reason="git binary is required")
def test_repository_launch_and_merge_keep_explicit_boundaries(tmp_path: Path) -> None:
    boundary, repository, _remote = _repository_fixture(tmp_path)
    with pytest.raises(ValueError, match="--repository PATH"):
        parse_focus_project_launch(
            "/project start --goal guess-the-repository",
            session_id="session",
            agent_id="agent",
            workspace_boundary=boundary,
            config_ref=None,
        )
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / ".git").mkdir()
    with pytest.raises(ValueError, match="inside the workspace boundary"):
        build_project_launch_request(
            goal="outside",
            session_id="session",
            agent_id="agent",
            workspace_boundary=boundary,
            repository=outside,
        )
    non_git = boundary / "non-git"
    non_git.mkdir()
    with pytest.raises(ValueError, match="does not contain .git"):
        build_project_launch_request(
            goal="non-git",
            session_id="session",
            agent_id="agent",
            workspace_boundary=boundary,
            repository=non_git,
        )

    store = AutonomyRunStore(root=tmp_path / "autonomy-negative")
    manager = TaskManager.for_lifecycle_db(db_path=tmp_path / "negative.db")
    launch = build_project_launch_request(
        goal="Do not merge without separate approval",
        session_id="session",
        agent_id="agent",
        workspace_boundary=boundary,
        repository=repository,
        max_iterations=1,
        verification_waiver_reason="Boundary-only negative proof.",
    )
    run = launch_project(launch, store=store, manager=manager)
    assert run.task_id is not None

    class MergeProvider:
        provider_id = DEFAULT_GITHUB_PROVIDER_ID

        def __init__(self) -> None:
            self.mutation_calls = 0

        def read_merge_pr(self, *, args: Mapping[str, Any], ctx: Any):
            del ctx
            return {
                "ok": True,
                "data": {
                    "owner": args["owner"],
                    "repo": args["repo"],
                    "number": args["number"],
                    "state": "open",
                    "head_sha": args["expected_head_sha"],
                    "merged": False,
                    "merge_commit_sha": "",
                },
                "source": {"provider_id": self.provider_id},
            }

        def fetch_checks(self, *, args: Mapping[str, Any], ctx: Any):
            del ctx
            return {
                "ok": True,
                "data": {
                    "head_sha": args["head_sha"],
                    "expected_checks": list(args["expected_checks"]),
                    "missing_expected_checks": [],
                    "overall_result": "success",
                },
                "source": {"provider_id": self.provider_id},
            }

        def merge_pr(self, *, args: Mapping[str, Any], ctx: Any):
            del args, ctx
            self.mutation_calls += 1
            raise AssertionError("unapproved merge reached the provider")

    merge_args = {
        "owner": "openminion",
        "repo": "fixture",
        "number": 17,
        "expected_head_sha": _git(repository, "rev-parse", "HEAD"),
        "merge_method": "squash",
        "expected_checks": ["lint", "tests"],
    }
    provider_registry().reset()
    provider = MergeProvider()
    register_provider(provider)
    try:
        registry = ToolRegistry()
        register_github(registry)
        result = ToolAdapter(
            workspace_root=repository,
            runtime_registry=registry,
            policy={"tools": {"allow_exact": ["github.merge_pr"]}},
            task_manager=manager,
            agent_id="agent",
            agent_profile=SimpleNamespace(
                provider_config_overrides={"github": {"allow_merge": True}}
            ),
        ).execute(
            command=_project_command(
                "github.merge_pr",
                merge_args,
                task_id=run.task_id,
                command_id="merge-without-grant",
            ),
            session_id=run.session_id,
            trace_id="merge-without-grant",
        )
    finally:
        provider_registry().reset()
    assert result["error"]["code"] == "POLICY_DENIED"
    assert provider.mutation_calls == 0
