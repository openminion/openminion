from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from openminion.modules.brain.adapters.tool import ToolAdapter
from openminion.modules.brain.adapters.tool.project_git import (
    git_push_action_scope,
    git_tag_push_action_scope,
)
from openminion.modules.task import (
    AutonomyRunPhase,
    AutonomyRunStatus,
    TaskManager,
    build_autonomy_run,
    build_project_run_projection,
    load_latest_project_checkpoint,
    save_project_run_checkpoint,
)
from openminion.modules.task.autonomy import build_local_workspace_ref, now_ms
from openminion.modules.task.project.effects import (
    ProjectEffectStatus,
    load_project_effect_record,
)
from openminion.modules.task.project.policy import (
    issue_project_permission_grant,
    load_project_policy_state,
)
from openminion.modules.tool.registry import ToolRegistry
from openminion.tools.git import register

_GIT = shutil.which("git")


def _run(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_GIT, *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _remote_repo(tmp_path: Path, name: str = "repo") -> tuple[Path, Path]:
    remote = tmp_path / f"{name}-remote.git"
    remote.mkdir()
    _run(["init", "-q", "--bare", "-b", "main"], cwd=remote)
    local = tmp_path / name
    local.mkdir()
    _run(["init", "-q", "-b", "main"], cwd=local)
    _run(["config", "user.email", "test@example.com"], cwd=local)
    _run(["config", "user.name", "Test User"], cwd=local)
    _run(["config", "commit.gpgsign", "false"], cwd=local)
    (local / "README.md").write_text("initial\n", encoding="utf-8")
    _run(["add", "README.md"], cwd=local)
    _run(["commit", "-q", "-m", "initial"], cwd=local)
    _run(["remote", "add", "origin", str(remote)], cwd=local)
    return local, remote


def _scope_context(workspace: Path) -> SimpleNamespace:
    return SimpleNamespace(
        policy=SimpleNamespace(raw={"workspace_root": str(workspace)}),
        workspace=workspace,
    )


def _project_manager(tmp_path: Path, workspace: Path) -> TaskManager:
    manager = TaskManager.for_lifecycle_db(db_path=tmp_path / "tasks.db")
    manager.create_task(
        session_id="session-1",
        mode_name="project",
        goal="publish repository changes",
        agent_id="agent-1",
        task_id="task-1",
    )
    autonomy_run = build_autonomy_run(
        goal_text="publish repository changes",
        goal_id="goal-1",
        session_id="session-1",
        workspace_ref=build_local_workspace_ref(workspace),
        max_iterations=3,
    ).model_copy(
        update={
            "task_id": "task-1",
            "checkpoint_id": "checkpoint-1",
            "status": AutonomyRunStatus.RUNNING,
            "phase": AutonomyRunPhase.EXECUTE,
        }
    )
    project_run = build_project_run_projection(
        autonomy_run,
        objective_ledger_ref="project:objective",
        evidence_ledger_ref="project:evidence",
        resume_packet_ref="project:resume",
        operator_decision_log_ref="project:decisions",
        capability_plan_ref="project:capabilities",
        metrics_summary_ref="project:metrics",
    )
    save_project_run_checkpoint(
        manager,
        project_run,
        checkpoint_id="checkpoint-1",
    )
    return manager


@dataclass
class _Telemetry:
    operations: list[dict[str, Any]] = field(default_factory=list)

    def emit_module_operation(
        self,
        session_id: str,
        turn_id: str,
        module_id: str,
        operation: str,
        **kwargs: Any,
    ) -> None:
        self.operations.append(
            {
                "session_id": session_id,
                "turn_id": turn_id,
                "module_id": module_id,
                "operation": operation,
                **kwargs,
            }
        )


def _adapter(
    workspace: Path,
    manager: TaskManager,
    *,
    telemetry: _Telemetry | None = None,
) -> ToolAdapter:
    registry = ToolRegistry()
    register(registry)
    return ToolAdapter(
        workspace_root=workspace,
        runtime_registry=registry,
        policy={
            "workspace_root": str(workspace),
            "tools": {"allow_exact": ["git.push", "git.tag"]},
        },
        task_manager=manager,
        telemetryctl=telemetry,
        agent_id="agent-1",
    )


def _command(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "tool_name": tool_name,
        "args": args,
        "idempotency_key": f"{tool_name}-1",
        "meta": {"orchestration": {"task_backed_task_id": "task-1"}},
    }


def _issue_grant(
    manager: TaskManager,
    *,
    tool_name: str,
    scope: str,
) -> None:
    issued = now_ms()
    issue_project_permission_grant(
        manager,
        task_id="task-1",
        grant_id=f"grant:{tool_name}",
        tool_name=tool_name,
        scope=scope,
        issued_at_ms=issued,
        expires_at_ms=issued + 60_000,
        max_uses=1,
    )


@pytest.mark.skipif(_GIT is None, reason="git binary not on PATH")
def test_project_push_denial_then_approved_receipt_and_telemetry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    workspace, remote = _remote_repo(tmp_path)
    manager = _project_manager(tmp_path, workspace)
    args = {
        "remote": "origin",
        "source_ref": "refs/heads/main",
        "target_ref": "refs/heads/main",
    }
    command = _command("git.push", args)
    denied = _adapter(workspace, manager).execute(
        command=command,
        session_id="session-1",
        trace_id="turn-1",
    )
    assert denied["error"]["code"] == "POLICY_DENIED"
    assert not (remote / "refs" / "heads" / "main").exists()

    scope = git_push_action_scope(args, _scope_context(workspace))
    _issue_grant(manager, tool_name="git.push", scope=scope)
    telemetry = _Telemetry()
    from openminion.tools.git import remote as git_remote

    original = git_remote.run_git
    observed: dict[str, Any] = {}

    def observe_started(args, **kwargs):
        if args[0] == "push":
            effect = load_project_effect_record(
                manager,
                task_id="task-1",
                effect_id="effect:git.push:git.push-1",
            )
            policy = load_project_policy_state(manager, task_id="task-1")
            observed.update(
                status=effect.status if effect else None,
                grant_uses=policy.grants[0].uses if policy else None,
            )
        return original(args, **kwargs)

    monkeypatch.setattr(git_remote, "run_git", observe_started)
    result = _adapter(workspace, manager, telemetry=telemetry).execute(
        command=command,
        session_id="session-1",
        trace_id="turn-2",
    )

    assert result["status"] == "success"
    assert observed == {"status": ProjectEffectStatus.STARTED, "grant_uses": 1}
    parsed = result["outputs"]["parsed"]
    assert parsed["project_effect_status"] == "succeeded"
    assert parsed["repository_action_scope"] == scope
    effect = load_project_effect_record(
        manager,
        task_id="task-1",
        effect_id="effect:git.push:git.push-1",
    )
    assert effect is not None and effect.status == ProjectEffectStatus.SUCCEEDED
    checkpoint = load_latest_project_checkpoint(manager, task_id="task-1")
    assert (
        checkpoint is not None
        and effect.effect_id in checkpoint.project_run.effect_refs
    )
    assert telemetry.operations[-1]["extra"]["project_effect_id"] == effect.effect_id


@pytest.mark.skipif(_GIT is None, reason="git binary not on PATH")
def test_uncertain_project_push_reconciles_after_restart_without_repeat(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    workspace, _ = _remote_repo(tmp_path)
    manager = _project_manager(tmp_path, workspace)
    args = {
        "remote": "origin",
        "source_ref": "refs/heads/main",
        "target_ref": "refs/heads/main",
    }
    scope = git_push_action_scope(args, _scope_context(workspace))
    _issue_grant(
        manager,
        tool_name="git.push",
        scope=scope,
    )
    command = _command("git.push", args)
    command.pop("idempotency_key")
    from openminion.tools.git import remote as git_remote

    original = git_remote.run_git
    push_calls = 0

    def push_then_timeout(args, **kwargs):
        nonlocal push_calls
        result = original(args, **kwargs)
        if args[0] == "push":
            push_calls += 1
            raise subprocess.TimeoutExpired(args, 30)
        return result

    monkeypatch.setattr(git_remote, "run_git", push_then_timeout)
    first = _adapter(workspace, manager).execute(
        command=command,
        session_id="session-1",
        trace_id="turn-1",
    )
    assert first["error"]["code"] == "GIT_REMOTE_OUTCOME_UNCERTAIN"
    effect_id = f"effect:git.push:{scope}"
    effect = load_project_effect_record(
        manager,
        task_id="task-1",
        effect_id=effect_id,
    )
    assert effect is not None and effect.status == ProjectEffectStatus.STARTED
    assert push_calls == 1
    manager.close()

    monkeypatch.setattr(git_remote, "run_git", original)
    restarted = TaskManager.for_lifecycle_db(db_path=tmp_path / "tasks.db")
    second = _adapter(workspace, restarted).execute(
        command=command,
        session_id="session-1",
        trace_id="turn-2",
    )
    assert second["status"] == "success"
    assert second["outputs"]["parsed"]["reconciled"] is True
    assert push_calls == 1
    effect = load_project_effect_record(
        restarted,
        task_id="task-1",
        effect_id=effect_id,
    )
    assert effect is not None and effect.status == ProjectEffectStatus.SUCCEEDED
    assert effect.verification_refs == ("git.push:readback",)


@pytest.mark.skipif(_GIT is None, reason="git binary not on PATH")
def test_project_tag_publication_uses_its_own_grant_and_effect(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    workspace, _ = _remote_repo(tmp_path)
    manager = _project_manager(tmp_path, workspace)
    adapter = _adapter(workspace, manager)
    created = adapter.execute(
        command=_command(
            "git.tag",
            {
                "action": "create",
                "name": "v1.0.0-rc1",
                "target_ref": "refs/heads/main",
                "message": "Release candidate 1",
            },
        ),
        session_id="session-1",
        trace_id="turn-1",
    )
    assert created["status"] == "success"
    args = {"action": "push", "name": "v1.0.0-rc1", "remote": "origin"}
    scope = git_tag_push_action_scope(args, _scope_context(workspace))
    _issue_grant(manager, tool_name="git.tag", scope=scope)

    published = adapter.execute(
        command=_command("git.tag", args),
        session_id="session-1",
        trace_id="turn-2",
    )

    assert published["status"] == "success"
    assert published["outputs"]["parsed"]["repository_action_scope"] == scope
    effect = load_project_effect_record(
        manager,
        task_id="task-1",
        effect_id="effect:git.tag:git.tag-1",
    )
    assert effect is not None and effect.capability_ref == "git.tag"


@pytest.mark.skipif(_GIT is None, reason="git binary not on PATH")
def test_uncertain_project_tag_publication_reconciles_without_repeat(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    workspace, _ = _remote_repo(tmp_path)
    _run(
        ["tag", "-a", "-m", "Release candidate 1", "v1.0.0-rc1", "main"],
        cwd=workspace,
    )
    manager = _project_manager(tmp_path, workspace)
    args = {"action": "push", "name": "v1.0.0-rc1", "remote": "origin"}
    _issue_grant(
        manager,
        tool_name="git.tag",
        scope=git_tag_push_action_scope(args, _scope_context(workspace)),
    )
    command = _command("git.tag", args)
    from openminion.tools.git import remote as git_remote

    original = git_remote.run_git
    push_calls = 0

    def push_then_timeout(args, **kwargs):
        nonlocal push_calls
        result = original(args, **kwargs)
        if args[0] == "push":
            push_calls += 1
            raise subprocess.TimeoutExpired(args, 30)
        return result

    monkeypatch.setattr(git_remote, "run_git", push_then_timeout)
    first = _adapter(workspace, manager).execute(
        command=command,
        session_id="session-1",
        trace_id="turn-1",
    )
    assert first["error"]["code"] == "GIT_REMOTE_OUTCOME_UNCERTAIN"
    assert push_calls == 1

    monkeypatch.setattr(git_remote, "run_git", original)
    second = _adapter(workspace, manager).execute(
        command=command,
        session_id="session-1",
        trace_id="turn-2",
    )
    assert second["status"] == "success"
    assert second["outputs"]["parsed"]["reconciled"] is True
    assert push_calls == 1
    effect = load_project_effect_record(
        manager,
        task_id="task-1",
        effect_id="effect:git.tag:git.tag-1",
    )
    assert effect is not None and effect.verification_refs == ("git.tag:readback",)


@pytest.mark.skipif(_GIT is None, reason="git binary not on PATH")
def test_project_git_action_cannot_escape_bound_repository(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    project_repo, _ = _remote_repo(tmp_path, "project")
    other_repo, other_remote = _remote_repo(tmp_path, "other")
    manager = _project_manager(tmp_path, project_repo)
    args = {
        "remote": "origin",
        "source_ref": "refs/heads/main",
        "target_ref": "refs/heads/main",
    }
    _issue_grant(
        manager,
        tool_name="git.push",
        scope=git_push_action_scope(args, _scope_context(other_repo)),
    )

    result = _adapter(other_repo, manager).execute(
        command=_command("git.push", args),
        session_id="session-1",
        trace_id="turn-1",
    )

    assert result["error"]["code"] == "POLICY_DENIED"
    assert result["error"]["details"]["reason_code"] == "project_repository_mismatch"
    assert not (other_remote / "refs" / "heads" / "main").exists()
