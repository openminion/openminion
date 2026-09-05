from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from openminion.modules.brain.adapters.tool import ToolAdapter
from openminion.modules.brain.adapters.tool.github_merge import (
    github_merge_pr_action_scope,
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
from openminion.modules.task.autonomy import now_ms
from openminion.modules.task.project.effects import (
    ProjectEffectStatus,
    load_project_effect_record,
)
from openminion.modules.task.project.policy import (
    issue_project_permission_grant,
    load_project_policy_state,
)
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.registry import ToolRegistry
from openminion.tools.github.constants import DEFAULT_GITHUB_PROVIDER_ID
from openminion.tools.github.plugin import register
from openminion.tools.github.providers import provider_registry, register_provider


_ARGS = {
    "owner": "openminion",
    "repo": "test-repo-for-agent",
    "number": 17,
    "expected_head_sha": "abc1234",
    "merge_method": "squash",
    "expected_checks": ["lint", "tests"],
}


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
        self.head_sha = str(_ARGS["expected_head_sha"])
        self.merged = False
        self.merge_commit_sha = ""
        self.mutation_calls = 0
        self.check_result = "success"
        self.missing_checks: list[str] = []
        self.return_uncertain = False
        self.return_http_status: int | None = None
        self.return_conflict = False
        self.result_overrides: dict[str, Any] = {}
        self.response_provider_id = self.provider_id
        self.on_merge: Callable[[], None] | None = None

    def read_merge_pr(self, *, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        del args, ctx
        return {
            "ok": True,
            "data": {
                "owner": _ARGS["owner"],
                "repo": _ARGS["repo"],
                "number": _ARGS["number"],
                "html_url": "https://github.com/openminion/test-repo-for-agent/pull/17",
                "state": "closed" if self.merged else "open",
                "head_sha": self.head_sha,
                "merged": self.merged,
                "merge_commit_sha": self.merge_commit_sha,
            },
            "source": {"provider_id": self.provider_id},
        }

    def fetch_checks(self, *, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        del ctx
        return {
            "ok": True,
            "data": {
                "head_sha": args["head_sha"],
                "expected_checks": list(args["expected_checks"]),
                "missing_expected_checks": list(self.missing_checks),
                "overall_result": self.check_result,
            },
            "source": {"provider_id": self.provider_id},
        }

    def merge_pr(self, *, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        reconciled = getattr(ctx, "github_merge_pr_reconciled_result", None)
        if isinstance(reconciled, Mapping):
            return dict(reconciled)
        self.mutation_calls += 1
        if self.on_merge is not None:
            self.on_merge()
        if self.return_uncertain:
            raise ToolRuntimeError(
                "UPSTREAM_ERROR",
                "GitHub REST API request failed.",
                {"reason_code": "github_api_unreachable"},
            )
        if self.return_http_status is not None:
            self.merged = True
            self.merge_commit_sha = "merge123"
            raise ToolRuntimeError(
                "UPSTREAM_ERROR",
                "GitHub REST API request failed.",
                {
                    "reason_code": "github_api_error",
                    "status_code": self.return_http_status,
                },
            )
        if self.return_conflict:
            raise ToolRuntimeError(
                "UPSTREAM_ERROR",
                "GitHub did not merge the pull request.",
                {"reason_code": "github_merge_pr_conflict"},
            )
        self.merged = True
        self.merge_commit_sha = "merge123"
        data = {
            "owner": args["owner"],
            "repo": args["repo"],
            "number": args["number"],
            "merged": True,
            "message": "Merged",
            "head_sha": args["expected_head_sha"],
            "merge_method": args["merge_method"],
            "merge_commit_sha": self.merge_commit_sha,
        }
        data.update(self.result_overrides)
        return {
            "ok": True,
            "data": data,
            "source": {"provider_id": self.response_provider_id},
        }


@pytest.fixture(autouse=True)
def _reset_provider_registry() -> None:
    provider_registry().reset()
    yield
    provider_registry().reset()


def _project_manager(tmp_path: Any) -> TaskManager:
    manager = TaskManager.for_lifecycle_db(db_path=tmp_path / "tasks.db")
    manager.create_task(
        session_id="session-1",
        mode_name="project",
        goal="merge one pull request",
        agent_id="agent-1",
        task_id="task-1",
    )
    autonomy_run = build_autonomy_run(
        goal_text="merge one pull request",
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
    save_project_run_checkpoint(
        manager,
        build_project_run_projection(
            autonomy_run,
            objective_ledger_ref="project:objective",
            evidence_ledger_ref="project:evidence",
            resume_packet_ref="project:resume",
            operator_decision_log_ref="project:decisions",
            capability_plan_ref="project:capabilities",
            metrics_summary_ref="project:metrics",
        ),
        checkpoint_id="checkpoint-1",
    )
    return manager


def _scope(**updates: Any) -> str:
    args = {**_ARGS, **updates}
    return github_merge_pr_action_scope(
        owner=str(args["owner"]),
        repo=str(args["repo"]),
        number=int(args["number"]),
        expected_head_sha=str(args["expected_head_sha"]),
        merge_method=str(args["merge_method"]),
        expected_checks=list(args["expected_checks"]),
    )


def _effect_id(**updates: Any) -> str:
    scope_hash = hashlib.sha256(_scope(**updates).encode()).hexdigest()
    return f"effect:github.merge_pr:{scope_hash}"


def _issue_grant(
    manager: TaskManager,
    *,
    scope: str | None = None,
    grant_id: str = "grant-1",
) -> None:
    issued = now_ms()
    issue_project_permission_grant(
        manager,
        task_id="task-1",
        grant_id=grant_id,
        tool_name="github.merge_pr",
        scope=scope or _scope(),
        issued_at_ms=issued,
        expires_at_ms=issued + 60_000,
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
        policy={"tools": {"allow_exact": ["github.merge_pr"]}},
        task_manager=manager,
        telemetryctl=telemetry,
        agent_id="agent-1",
        agent_profile=SimpleNamespace(
            provider_config_overrides={"github": {"allow_merge": True}}
        ),
    )


def _command(*, command_id: str = "merge-pr-1", **updates: Any) -> dict[str, Any]:
    return {
        "tool_name": "github.merge_pr",
        "args": {**_ARGS, **updates},
        "idempotency_key": command_id,
        "meta": {"orchestration": {"task_backed_task_id": "task-1"}},
    }


def test_project_merge_pr_persists_started_before_approved_merge(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    manager = _project_manager(tmp_path)
    _issue_grant(manager)
    provider = _ProjectGithubProvider()
    telemetry = _Telemetry()
    observed: dict[str, Any] = {}

    def observe_invocation() -> None:
        effect = load_project_effect_record(
            manager,
            task_id="task-1",
            effect_id=_effect_id(),
        )
        policy = load_project_policy_state(manager, task_id="task-1")
        observed.update(
            effect_status=effect.status if effect is not None else None,
            grant_uses=policy.grants[0].uses if policy is not None else None,
        )

    provider.on_merge = observe_invocation
    register_provider(provider)
    result = _adapter(tmp_path, manager, telemetry=telemetry).execute(
        command=_command(),
        session_id="session-1",
        trace_id="turn-1",
    )

    assert result["status"] == "success"
    assert provider.mutation_calls == 1
    assert observed == {
        "effect_status": ProjectEffectStatus.STARTED,
        "grant_uses": 1,
    }
    output = result["outputs"]["data"]
    assert output["merge_commit_sha"] == "merge123"
    assert output["project_effect_status"] == "succeeded"
    effect = load_project_effect_record(
        manager,
        task_id="task-1",
        effect_id=_effect_id(),
    )
    assert effect is not None and effect.status == ProjectEffectStatus.SUCCEEDED
    assert effect.idempotency_key == _scope()
    assert effect.result_ref.endswith("#17@merge123")
    checkpoint = load_latest_project_checkpoint(manager, task_id="task-1")
    assert checkpoint is not None
    receipt = checkpoint.payload["project_effect_receipts"][effect.effect_id]
    assert receipt == {
        "owner": "openminion",
        "repo": "test-repo-for-agent",
        "number": 17,
        "merged": True,
        "message": "Merged",
        "head_sha": "abc1234",
        "merge_method": "squash",
        "merge_commit_sha": "merge123",
        "provider_id": DEFAULT_GITHUB_PROVIDER_ID,
    }
    assert [item["operation"] for item in telemetry.operations] == [
        "invoke",
        "completed",
    ]
    completed = telemetry.operations[-1]
    assert completed["session_id"] == "session-1"
    assert completed["turn_id"] == "turn-1"
    assert completed["status"] == "ok"
    assert completed["extra"] == {
        "tool": "github.merge_pr",
        "project_task_id": "task-1",
        "project_run_id": checkpoint.project_run.project_run_id,
        "project_permission_grant_id": "grant-1",
        "project_effect_id": _effect_id(),
        "project_effect_status": "succeeded",
        "repository_action_scope": _scope(),
        "repository_owner": "openminion",
        "repository_name": "test-repo-for-agent",
        "repository_head_sha": "abc1234",
        "repository_pr_number": 17,
        "repository_merge_method": "squash",
        "repository_expected_checks": ["lint", "tests"],
        "project_effect_reconciled": False,
    }


def test_project_merge_pr_rejects_stale_head_sha(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    manager = _project_manager(tmp_path)
    _issue_grant(manager, scope=_scope(expected_head_sha="def5678"))
    provider = _ProjectGithubProvider()
    register_provider(provider)

    result = _adapter(tmp_path, manager).execute(
        command=_command(expected_head_sha="def5678"),
        session_id="session-1",
        trace_id="turn-1",
    )

    assert result["error"]["code"] == "INVALID_REQUEST"
    assert result["error"]["details"]["reason_code"] == "github_merge_pr_stale_head"
    assert provider.mutation_calls == 0


@pytest.mark.parametrize(
    ("overall", "missing", "reason"),
    [
        ("failure", [], "github_merge_pr_checks_failed"),
        ("pending", [], "github_merge_pr_checks_pending"),
        ("pending", ["tests"], "github_merge_pr_expected_checks_missing"),
    ],
)
def test_project_merge_pr_requires_every_expected_check_green(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
    overall: str,
    missing: list[str],
    reason: str,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    manager = _project_manager(tmp_path)
    provider = _ProjectGithubProvider()
    provider.check_result = overall
    provider.missing_checks = missing
    register_provider(provider)

    result = _adapter(tmp_path, manager).execute(
        command=_command(), session_id="session-1", trace_id="turn-1"
    )

    assert result["error"]["code"] == "INVALID_REQUEST"
    assert result["error"]["details"]["reason_code"] == reason
    assert provider.mutation_calls == 0


def test_project_merge_pr_denies_without_exact_grant(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    manager = _project_manager(tmp_path)
    provider = _ProjectGithubProvider()
    telemetry = _Telemetry()
    register_provider(provider)

    result = _adapter(tmp_path, manager, telemetry=telemetry).execute(
        command=_command(), session_id="session-1", trace_id="turn-1"
    )

    assert result["error"]["code"] == "POLICY_DENIED"
    assert provider.mutation_calls == 0
    assert telemetry.operations[0]["operation"] == "blocked_by_policy"
    assert telemetry.operations[0]["extra"]["repository_action_scope"] == _scope()


def test_project_merge_pr_rejects_stale_grant_scope(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    manager = _project_manager(tmp_path)
    _issue_grant(manager, scope=_scope(merge_method="rebase"))
    provider = _ProjectGithubProvider()
    register_provider(provider)

    result = _adapter(tmp_path, manager).execute(
        command=_command(), session_id="session-1", trace_id="turn-1"
    )

    assert result["error"]["code"] == "POLICY_DENIED"
    assert provider.mutation_calls == 0


def test_project_merge_pr_records_provider_conflict_as_terminal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    manager = _project_manager(tmp_path)
    _issue_grant(manager)
    provider = _ProjectGithubProvider()
    provider.return_conflict = True
    register_provider(provider)

    result = _adapter(tmp_path, manager).execute(
        command=_command(), session_id="session-1", trace_id="turn-1"
    )

    assert result["error"]["code"] == "UPSTREAM_ERROR"
    assert result["error"]["details"]["reason_code"] == "github_merge_pr_conflict"
    effect = load_project_effect_record(
        manager,
        task_id="task-1",
        effect_id=_effect_id(),
    )
    assert effect is not None and effect.status == ProjectEffectStatus.FAILED


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("owner", "someone-else", "github_merge_pr_result_mismatch"),
        ("repo", "other-repo", "github_merge_pr_result_mismatch"),
        ("number", 18, "github_merge_pr_result_mismatch"),
        ("head_sha", "def5678", "github_merge_pr_result_mismatch"),
        ("merge_method", "merge", "github_merge_pr_result_mismatch"),
        ("merge_commit_sha", "", "github_merge_pr_result_incomplete"),
        ("provider_id", "", "github_merge_pr_result_incomplete"),
    ],
)
def test_project_merge_pr_rejects_incomplete_or_mismatched_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
    field: str,
    value: Any,
    reason: str,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    manager = _project_manager(tmp_path)
    _issue_grant(manager)
    provider = _ProjectGithubProvider()
    if field == "provider_id":
        provider.response_provider_id = str(value)
    else:
        provider.result_overrides[field] = value
    register_provider(provider)

    result = _adapter(tmp_path, manager).execute(
        command=_command(), session_id="session-1", trace_id="turn-1"
    )

    assert result["error"]["code"] == "INVALID_RESPONSE"
    assert result["error"]["details"]["reason_code"] == reason
    assert result["error"]["details"]["project_effect_uncertain"] is True
    assert provider.mutation_calls == 1
    effect = load_project_effect_record(
        manager,
        task_id="task-1",
        effect_id=_effect_id(),
    )
    assert effect is not None and effect.status == ProjectEffectStatus.STARTED

    reconciled = _adapter(tmp_path, manager).execute(
        command=_command(command_id="receipt-readback"),
        session_id="session-1",
        trace_id="turn-2",
    )

    assert reconciled["status"] == "success"
    assert reconciled["outputs"]["data"]["reconciled"] is True
    assert provider.mutation_calls == 1


def test_uncertain_merge_reconciles_after_restart_without_repeat(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    manager = _project_manager(tmp_path)
    _issue_grant(manager)
    provider = _ProjectGithubProvider()
    provider.return_uncertain = True
    first_telemetry = _Telemetry()
    register_provider(provider)

    first = _adapter(tmp_path, manager, telemetry=first_telemetry).execute(
        command=_command(command_id="attempt-1"),
        session_id="session-1",
        trace_id="turn-1",
    )

    assert first["error"]["code"] == "UPSTREAM_ERROR"
    effect_id = _effect_id()
    effect = load_project_effect_record(manager, task_id="task-1", effect_id=effect_id)
    assert effect is not None and effect.status == ProjectEffectStatus.STARTED
    assert first_telemetry.operations[-1]["extra"]["error_code"] == (
        "PROJECT_EFFECT_UNCERTAIN"
    )
    manager.close()

    provider.return_uncertain = False
    provider.merged = True
    provider.merge_commit_sha = "merge123"
    restarted = TaskManager.for_lifecycle_db(db_path=tmp_path / "tasks.db")
    second_telemetry = _Telemetry()
    second = _adapter(tmp_path, restarted, telemetry=second_telemetry).execute(
        command=_command(
            command_id="attempt-2",
            expected_checks=["tests", "lint"],
        ),
        session_id="session-1",
        trace_id="turn-2",
    )

    assert second["status"] == "success"
    assert second["outputs"]["data"]["reconciled"] is True
    assert provider.mutation_calls == 1
    effect = load_project_effect_record(
        restarted, task_id="task-1", effect_id=effect_id
    )
    assert effect is not None
    assert effect.verification_refs == ("github.merge_pr:readback",)
    completed = second_telemetry.operations[-1]
    assert completed["session_id"] == "session-1"
    assert completed["turn_id"] == "turn-2"
    assert completed["extra"]["project_effect_id"] == effect_id
    assert completed["extra"]["project_effect_status"] == "succeeded"
    assert completed["extra"]["project_effect_reconciled"] is True


def test_http_5xx_merge_reconciles_without_second_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    manager = _project_manager(tmp_path)
    _issue_grant(manager)
    provider = _ProjectGithubProvider()
    provider.return_http_status = 502
    register_provider(provider)

    first = _adapter(tmp_path, manager).execute(
        command=_command(command_id="attempt-1"),
        session_id="session-1",
        trace_id="turn-1",
    )

    assert first["error"]["code"] == "UPSTREAM_ERROR"
    assert first["error"]["details"]["reason_code"] == "github_api_error"
    assert first["error"]["details"]["project_effect_uncertain"] is True
    effect = load_project_effect_record(
        manager,
        task_id="task-1",
        effect_id=_effect_id(),
    )
    assert effect is not None and effect.status == ProjectEffectStatus.STARTED
    assert provider.mutation_calls == 1

    provider.return_http_status = None
    _issue_grant(manager, grant_id="grant-2")
    second = _adapter(tmp_path, manager).execute(
        command=_command(
            command_id="attempt-2",
            expected_checks=["tests", "lint"],
        ),
        session_id="session-1",
        trace_id="turn-2",
    )

    assert second["status"] == "success"
    assert second["outputs"]["data"]["reconciled"] is True
    assert provider.mutation_calls == 1
    policy = load_project_policy_state(manager, task_id="task-1")
    assert policy is not None
    grant_uses = {grant.grant_id: grant.uses for grant in policy.grants}
    assert grant_uses == {"grant-1": 1, "grant-2": 0}


def test_uncertain_unmerged_pr_is_not_repeated(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    manager = _project_manager(tmp_path)
    _issue_grant(manager)
    provider = _ProjectGithubProvider()
    provider.return_uncertain = True
    register_provider(provider)
    _adapter(tmp_path, manager).execute(
        command=_command(command_id="attempt-1"),
        session_id="session-1",
        trace_id="turn-1",
    )
    provider.return_uncertain = False

    second = _adapter(tmp_path, manager).execute(
        command=_command(command_id="attempt-2"),
        session_id="session-1",
        trace_id="turn-2",
    )

    assert second["error"]["code"] == "UPSTREAM_ERROR"
    assert second["error"]["details"]["reason_code"] == (
        "github_merge_pr_readback_not_merged"
    )
    assert provider.mutation_calls == 1


def test_non_project_merge_pr_keeps_confirmation_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    manager = _project_manager(tmp_path)
    provider = _ProjectGithubProvider()
    register_provider(provider)

    result = _adapter(tmp_path, manager).execute(
        command={"tool_name": "github.merge_pr", "args": dict(_ARGS)},
        session_id="session-1",
        trace_id="turn-1",
    )

    assert result["status"] == "needs_user"
    assert result["error"]["code"] == "CONFIRM_REQUIRED"
    assert provider.mutation_calls == 0
