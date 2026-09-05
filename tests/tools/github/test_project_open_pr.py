from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Callable
from typing import Any, Mapping

import pytest

from openminion.modules.brain.adapters.tool import ToolAdapter
from openminion.modules.brain.adapters.tool.project_github import (
    github_open_pr_action_scope,
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
from openminion.modules.task.project.effects import (
    ProjectEffectStatus,
    load_project_effect_record,
)
from openminion.modules.task.project.policy import (
    issue_project_permission_grant,
    load_project_policy_state,
)
from openminion.modules.task.autonomy import now_ms
from openminion.modules.tool.registry import ToolRegistry
from openminion.tools.github.constants import DEFAULT_GITHUB_PROVIDER_ID
from openminion.tools.github.plugin import register
from openminion.tools.github.providers import provider_registry, register_provider


_ARGS = {
    "owner": "openminion",
    "repo": "test-repo-for-agent",
    "head": "openminion-smoke/orel-04",
    "base": "main",
    "title": "OREL-04",
    "body": "Project-owned pull request",
}
_HEAD_SHA = "abc1234"


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


class _ProjectGithubProvider:
    provider_id = DEFAULT_GITHUB_PROVIDER_ID

    def __init__(self) -> None:
        self.mutation_calls = 0
        self.readback: dict[str, Any] | None = None
        self.return_uncertain = False
        self.head_sha = _HEAD_SHA
        self.on_open_pr: Callable[[], None] | None = None

    def resolve_open_pr_head_sha(
        self, *, args: Mapping[str, Any], ctx: Any
    ) -> str:
        del args, ctx
        return self.head_sha

    def find_open_pr(
        self,
        *,
        args: Mapping[str, Any],
        ctx: Any,
        head_sha: str,
    ) -> Mapping[str, Any] | None:
        del args, ctx
        assert head_sha == _HEAD_SHA
        return self.readback

    def open_pr(self, *, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        reconciled = getattr(ctx, "github_open_pr_reconciled_result", None)
        if isinstance(reconciled, Mapping):
            return dict(reconciled)
        self.mutation_calls += 1
        if self.on_open_pr is not None:
            self.on_open_pr()
        if self.return_uncertain:
            return {
                "ok": False,
                "error": {
                    "code": "REMOTE_ERROR",
                    "message": "GitHub REST API request failed.",
                    "details": {"reason_code": "github_api_unreachable"},
                },
            }
        return _open_pr_result()


@pytest.fixture(autouse=True)
def _reset_provider_registry() -> None:
    provider_registry().reset()
    yield
    provider_registry().reset()


def _open_pr_result() -> dict[str, Any]:
    return {
        "ok": True,
        "data": {
            "owner": _ARGS["owner"],
            "repo": _ARGS["repo"],
            "number": 17,
            "html_url": "https://github.com/openminion/test-repo-for-agent/pull/17",
            "head": _ARGS["head"],
            "base": _ARGS["base"],
            "head_sha": _HEAD_SHA,
            "state": "open",
        },
        "source": {"provider_id": DEFAULT_GITHUB_PROVIDER_ID},
    }


def _project_manager(tmp_path: Any) -> TaskManager:
    manager = TaskManager.for_lifecycle_db(db_path=tmp_path / "tasks.db")
    manager.create_task(
        session_id="session-1",
        mode_name="project",
        goal="open one pull request",
        agent_id="agent-1",
        task_id="task-1",
    )
    autonomy_run = build_autonomy_run(
        goal_text="open one pull request",
        goal_id="goal-1",
        session_id="session-1",
        workspace_ref="local:/workspace#commit=abc;dirty=clean",
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


def _scope() -> str:
    return github_open_pr_action_scope(
        owner=str(_ARGS["owner"]),
        repo=str(_ARGS["repo"]),
        head=str(_ARGS["head"]),
        base=str(_ARGS["base"]),
        head_sha=_HEAD_SHA,
    )


def _issue_grant(
    manager: TaskManager,
    *,
    grant_id: str = "grant-1",
    issued_at_ms: int | None = None,
    expires_at_ms: int | None = None,
) -> None:
    issued = now_ms() if issued_at_ms is None else issued_at_ms
    issue_project_permission_grant(
        manager,
        task_id="task-1",
        grant_id=grant_id,
        tool_name="github.open_pr",
        scope=_scope(),
        issued_at_ms=issued,
        expires_at_ms=expires_at_ms or issued + 60_000,
        max_uses=1,
    )


def _adapter(
    tmp_path: Any,
    manager: TaskManager,
    *,
    telemetry: _Telemetry | None = None,
) -> ToolAdapter:
    registry = ToolRegistry()
    register(registry)
    return ToolAdapter(
        workspace_root=tmp_path,
        runtime_registry=registry,
        policy={"tools": {"allow_exact": ["github.open_pr"]}},
        task_manager=manager,
        telemetryctl=telemetry,
        agent_id="agent-1",
    )


def _command(*, idempotency_key: str = "open-pr-1") -> dict[str, Any]:
    command: dict[str, Any] = {
        "tool_name": "github.open_pr",
        "args": dict(_ARGS),
        "meta": {"orchestration": {"task_backed_task_id": "task-1"}},
    }
    if idempotency_key:
        command["idempotency_key"] = idempotency_key
    return command


def test_project_open_pr_consumes_grant_and_persists_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    manager = _project_manager(tmp_path)
    _issue_grant(manager)
    provider = _ProjectGithubProvider()
    register_provider(provider)
    telemetry = _Telemetry()
    observed_at_invocation: dict[str, Any] = {}

    def observe_started_state() -> None:
        effect = load_project_effect_record(
            manager,
            task_id="task-1",
            effect_id="effect:github.open_pr:open-pr-1",
        )
        policy = load_project_policy_state(manager, task_id="task-1")
        observed_at_invocation.update(
            effect_status=effect.status if effect is not None else None,
            grant_uses=policy.grants[0].uses if policy is not None else None,
        )

    provider.on_open_pr = observe_started_state

    result = _adapter(tmp_path, manager, telemetry=telemetry).execute(
        command=_command(),
        session_id="session-1",
        trace_id="turn-1",
    )

    assert result["status"] == "success"
    assert provider.mutation_calls == 1
    output = result["outputs"]["data"]
    assert output["owner"] == _ARGS["owner"]
    assert output["repo"] == _ARGS["repo"]
    assert output["project_permission_grant_id"] == "grant-1"
    assert output["project_effect_status"] == "succeeded"
    assert observed_at_invocation == {
        "effect_status": ProjectEffectStatus.STARTED,
        "grant_uses": 1,
    }
    effect = load_project_effect_record(
        manager,
        task_id="task-1",
        effect_id="effect:github.open_pr:open-pr-1",
    )
    assert effect is not None
    assert effect.status == ProjectEffectStatus.SUCCEEDED
    assert effect.result_ref == "github:pull:openminion/test-repo-for-agent#17"
    checkpoint = load_latest_project_checkpoint(manager, task_id="task-1")
    assert checkpoint is not None
    assert effect.effect_id in checkpoint.project_run.effect_refs
    receipts = checkpoint.payload["project_effect_receipts"]
    assert receipts[effect.effect_id]["head_sha"] == _HEAD_SHA
    policy = load_project_policy_state(manager, task_id="task-1")
    assert policy is not None and policy.grants[0].uses == 1
    assert telemetry.operations[-1]["extra"]["project_run_id"]
    assert telemetry.operations[-1]["extra"]["repository_head_sha"] == _HEAD_SHA


def test_project_open_pr_denial_skips_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    manager = _project_manager(tmp_path)
    provider = _ProjectGithubProvider()
    register_provider(provider)

    result = _adapter(tmp_path, manager).execute(
        command=_command(),
        session_id="session-1",
        trace_id="turn-1",
    )

    assert result["status"] == "error"
    assert result["error"]["code"] == "POLICY_DENIED"
    assert result["error"]["details"]["repository_action_scope"] == _scope()
    assert provider.mutation_calls == 0


def test_newer_project_grant_supersedes_expired_matching_grant(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    manager = _project_manager(tmp_path)
    now = now_ms()
    _issue_grant(
        manager,
        grant_id="expired-grant",
        issued_at_ms=now - 2_000,
        expires_at_ms=now - 1_000,
    )
    _issue_grant(manager, grant_id="current-grant", issued_at_ms=now)
    provider = _ProjectGithubProvider()
    register_provider(provider)

    result = _adapter(tmp_path, manager).execute(
        command=_command(),
        session_id="session-1",
        trace_id="turn-1",
    )

    assert result["status"] == "success"
    policy = load_project_policy_state(manager, task_id="task-1")
    assert policy is not None
    assert [(grant.grant_id, grant.uses) for grant in policy.grants] == [
        ("expired-grant", 0),
        ("current-grant", 1),
    ]


def test_expired_only_project_grant_denies_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    manager = _project_manager(tmp_path)
    now = now_ms()
    _issue_grant(
        manager,
        grant_id="expired-grant",
        issued_at_ms=now - 2_000,
        expires_at_ms=now - 1_000,
    )
    provider = _ProjectGithubProvider()
    register_provider(provider)

    result = _adapter(tmp_path, manager).execute(
        command=_command(),
        session_id="session-1",
        trace_id="turn-1",
    )

    assert result["error"]["code"] == "POLICY_DENIED"
    assert result["error"]["details"]["reason_code"] == "expired"
    assert provider.mutation_calls == 0


def test_uncertain_open_pr_stays_started_and_reconciles_after_restart(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    manager = _project_manager(tmp_path)
    _issue_grant(manager)
    provider = _ProjectGithubProvider()
    provider.return_uncertain = True
    register_provider(provider)
    command = _command()

    first = _adapter(tmp_path, manager).execute(
        command=command,
        session_id="session-1",
        trace_id="turn-1",
    )

    assert first["error"]["code"] == "UPSTREAM_ERROR"
    assert first["error"]["details"]["provider_error_code"] == "REMOTE_ERROR"
    assert first["error"]["details"]["project_effect_uncertain"] is True
    effect_id = "effect:github.open_pr:open-pr-1"
    effect = load_project_effect_record(
        manager,
        task_id="task-1",
        effect_id=effect_id,
    )
    assert effect is not None and effect.status == ProjectEffectStatus.STARTED
    assert provider.mutation_calls == 1
    manager.close()

    restarted = TaskManager.for_lifecycle_db(db_path=tmp_path / "tasks.db")
    provider.return_uncertain = False
    provider.readback = _open_pr_result()
    second = _adapter(tmp_path, restarted).execute(
        command=command,
        session_id="session-1",
        trace_id="turn-2",
    )

    assert second["status"] == "success"
    assert second["outputs"]["data"]["reconciled"] is True
    assert provider.mutation_calls == 1
    effect = load_project_effect_record(
        restarted,
        task_id="task-1",
        effect_id=effect_id,
    )
    assert effect is not None and effect.status == ProjectEffectStatus.SUCCEEDED
    assert effect.verification_refs == ("github.open_pr:readback",)


def test_missing_command_id_uses_exact_action_scope_as_stable_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    manager = _project_manager(tmp_path)
    _issue_grant(manager)
    register_provider(_ProjectGithubProvider())

    result = _adapter(tmp_path, manager).execute(
        command=_command(idempotency_key=""),
        session_id="session-1",
        trace_id="turn-1",
    )

    assert result["status"] == "success"
    effect_id = f"effect:github.open_pr:{_scope()}"
    effect = load_project_effect_record(
        manager,
        task_id="task-1",
        effect_id=effect_id,
    )
    assert effect is not None and effect.idempotency_key == _scope()


def test_changed_head_sha_blocks_stale_open_pr_replay(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    manager = _project_manager(tmp_path)
    _issue_grant(manager)
    provider = _ProjectGithubProvider()
    provider.return_uncertain = True
    register_provider(provider)
    command = _command()
    _adapter(tmp_path, manager).execute(
        command=command,
        session_id="session-1",
        trace_id="turn-1",
    )
    provider.return_uncertain = False
    provider.head_sha = "def5678"

    result = _adapter(tmp_path, manager).execute(
        command=command,
        session_id="session-1",
        trace_id="turn-2",
    )

    assert result["error"]["code"] == "INVALID_REQUEST"
    assert result["error"]["details"]["reason_code"] == "precondition_refs_changed"
    assert provider.mutation_calls == 1


def test_non_project_open_pr_keeps_existing_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    provider = _ProjectGithubProvider()
    register_provider(provider)
    manager = _project_manager(tmp_path)

    result = _adapter(tmp_path, manager).execute(
        command={"tool_name": "github.open_pr", "args": dict(_ARGS)},
        session_id="session-1",
        trace_id="turn-1",
    )

    assert result["status"] == "needs_user"
    assert result["error"]["code"] == "CONFIRM_REQUIRED"
    assert provider.mutation_calls == 0
