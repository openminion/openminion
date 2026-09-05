from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import pytest

from openminion.modules.brain.adapters.tool import ToolAdapter
from openminion.modules.brain.adapters.tool.github_update import (
    github_update_pr_action_scope,
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
    load_project_effect_receipt,
    load_project_effect_record,
)
from openminion.modules.task.project.policy import (
    issue_project_permission_grant,
    load_project_policy_state,
)
from openminion.modules.tool.registry import ToolRegistry
from openminion.tools.github.constants import DEFAULT_GITHUB_PROVIDER_ID
from openminion.tools.github.plugin import register
from openminion.tools.github.providers import provider_registry, register_provider


_ARGS = {
    "owner": "openminion",
    "repo": "test-repo-for-agent",
    "number": 17,
    "title": "Updated title",
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
        self.head_sha = _HEAD_SHA
        self.title = "Original title"
        self.body = "Original body"
        self.mutation_calls = 0
        self.return_uncertain = False
        self.return_error = False
        self.return_malformed_success = False
        self.preflight_error = ""
        self.on_update: Callable[[], None] | None = None

    def read_update_pr(self, *, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        del args, ctx
        result = self._result()
        if self.preflight_error == "missing_data":
            result.pop("data")
        elif self.preflight_error == "missing_head_sha":
            result["data"].pop("head_sha")
        return result

    def update_pr(self, *, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        reconciled = getattr(ctx, "github_update_pr_reconciled_result", None)
        if isinstance(reconciled, Mapping):
            return dict(reconciled)
        self.mutation_calls += 1
        if self.on_update is not None:
            self.on_update()
        if self.return_uncertain:
            return {
                "ok": False,
                "error": {
                    "code": "REMOTE_ERROR",
                    "message": "GitHub REST API request failed.",
                    "details": {"reason_code": "github_api_unreachable"},
                },
            }
        if self.return_error:
            return {
                "ok": False,
                "error": {
                    "code": "REMOTE_ERROR",
                    "message": "GitHub REST API request failed.",
                    "details": {"reason_code": "github_api_error"},
                },
            }
        if self.return_malformed_success:
            return {"ok": True}
        if args.get("title") is not None:
            self.title = str(args["title"])
        if args.get("body") is not None:
            self.body = str(args["body"])
        return self._result()

    def _result(self) -> dict[str, Any]:
        return {
            "ok": True,
            "data": {
                "owner": _ARGS["owner"],
                "repo": _ARGS["repo"],
                "number": _ARGS["number"],
                "html_url": "https://github.com/openminion/test-repo-for-agent/pull/17",
                "title": self.title,
                "body": self.body,
                "state": "open",
                "head_sha": self.head_sha,
            },
            "source": {"provider_id": self.provider_id},
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
        goal="update one pull request",
        agent_id="agent-1",
        task_id="task-1",
    )
    autonomy_run = build_autonomy_run(
        goal_text="update one pull request",
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


def _scope(*, head_sha: str = _HEAD_SHA) -> str:
    return github_update_pr_action_scope(
        owner=str(_ARGS["owner"]),
        repo=str(_ARGS["repo"]),
        number=int(_ARGS["number"]),
        head_sha=head_sha,
        title=str(_ARGS["title"]),
        body=None,
    )


def _issue_grant(manager: TaskManager, *, scope: str | None = None) -> None:
    issued = now_ms()
    issue_project_permission_grant(
        manager,
        task_id="task-1",
        grant_id="grant-1",
        tool_name="github.update_pr",
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
        policy={"tools": {"allow_exact": ["github.update_pr"]}},
        task_manager=manager,
        telemetryctl=telemetry,
        agent_id="agent-1",
    )


def _command() -> dict[str, Any]:
    return {
        "tool_name": "github.update_pr",
        "args": dict(_ARGS),
        "idempotency_key": "update-pr-1",
        "meta": {"orchestration": {"task_backed_task_id": "task-1"}},
    }


def test_project_update_pr_persists_effect_after_consuming_exact_grant(
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
            effect_id="effect:github.update_pr:update-pr-1",
        )
        policy = load_project_policy_state(manager, task_id="task-1")
        observed.update(
            effect_status=effect.status if effect is not None else None,
            grant_uses=policy.grants[0].uses if policy is not None else None,
        )

    provider.on_update = observe_invocation
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
    assert output["title"] == _ARGS["title"]
    assert output["project_effect_status"] == "succeeded"
    effect = load_project_effect_record(
        manager,
        task_id="task-1",
        effect_id="effect:github.update_pr:update-pr-1",
    )
    assert effect is not None and effect.status == ProjectEffectStatus.SUCCEEDED
    assert effect.result_ref == "github:pull:openminion/test-repo-for-agent#17"
    checkpoint = load_latest_project_checkpoint(manager, task_id="task-1")
    assert checkpoint is not None
    assert (
        checkpoint.payload["project_effect_receipts"][effect.effect_id]["title"]
        == _ARGS["title"]
    )
    assert [item["operation"] for item in telemetry.operations] == [
        "invoke",
        "completed",
    ]
    started, completed = telemetry.operations
    assert started["extra"]["project_effect_status"] == "started"
    assert completed["session_id"] == "session-1"
    assert completed["turn_id"] == "turn-1"
    assert completed["status"] == "ok"
    assert completed["extra"] == {
        "tool": "github.update_pr",
        "project_task_id": "task-1",
        "project_run_id": checkpoint.project_run.project_run_id,
        "project_permission_grant_id": "grant-1",
        "project_effect_id": "effect:github.update_pr:update-pr-1",
        "project_effect_status": "succeeded",
        "repository_action_scope": _scope(),
        "repository_head_sha": _HEAD_SHA,
        "repository_pr_number": 17,
        "project_effect_reconciled": False,
    }


def test_project_update_pr_denies_without_matching_grant(
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
    assert len(telemetry.operations) == 1
    blocked = telemetry.operations[0]
    assert blocked["operation"] == "blocked_by_policy"
    assert blocked["status"] == "error"
    assert blocked["extra"]["tool"] == "github.update_pr"
    assert blocked["extra"]["error_code"] == "POLICY_DENIED"
    assert blocked["extra"]["project_task_id"] == "task-1"
    assert blocked["extra"]["repository_action_scope"] == _scope()


def test_project_update_pr_rejects_approval_for_stale_head(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    manager = _project_manager(tmp_path)
    _issue_grant(manager, scope=_scope(head_sha="old1234"))
    provider = _ProjectGithubProvider()
    register_provider(provider)

    result = _adapter(tmp_path, manager).execute(
        command=_command(), session_id="session-1", trace_id="turn-1"
    )

    assert result["error"]["code"] == "POLICY_DENIED"
    assert provider.mutation_calls == 0


def test_uncertain_update_reconciles_after_restart_without_repeat(
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
    first_telemetry = _Telemetry()

    first = _adapter(tmp_path, manager, telemetry=first_telemetry).execute(
        command=command, session_id="session-1", trace_id="turn-1"
    )

    assert first["error"]["code"] == "UPSTREAM_ERROR"
    assert first["error"]["details"]["provider_error_code"] == "REMOTE_ERROR"
    effect_id = "effect:github.update_pr:update-pr-1"
    effect = load_project_effect_record(manager, task_id="task-1", effect_id=effect_id)
    assert effect is not None and effect.status == ProjectEffectStatus.STARTED
    assert [item["operation"] for item in first_telemetry.operations] == [
        "invoke",
        "completed",
    ]
    uncertain = first_telemetry.operations[-1]
    assert uncertain["status"] == "error"
    assert uncertain["extra"]["tool"] == "github.update_pr"
    assert uncertain["extra"]["error_code"] == "PROJECT_EFFECT_UNCERTAIN"
    assert uncertain["extra"]["project_effect_uncertain"] is True
    assert uncertain["extra"]["project_effect_status"] == "started"
    manager.close()

    restarted = TaskManager.for_lifecycle_db(db_path=tmp_path / "tasks.db")
    provider.return_uncertain = False
    provider.title = str(_ARGS["title"])
    second_telemetry = _Telemetry()
    second = _adapter(tmp_path, restarted, telemetry=second_telemetry).execute(
        command=command, session_id="session-1", trace_id="turn-2"
    )

    assert second["status"] == "success"
    assert second["outputs"]["data"]["reconciled"] is True
    assert provider.mutation_calls == 1
    effect = load_project_effect_record(
        restarted, task_id="task-1", effect_id=effect_id
    )
    assert effect is not None
    assert effect.verification_refs == ("github.update_pr:readback",)
    assert [item["operation"] for item in second_telemetry.operations] == [
        "invoke",
        "completed",
    ]
    reconciled = second_telemetry.operations[-1]
    assert reconciled["session_id"] == "session-1"
    assert reconciled["turn_id"] == "turn-2"
    assert reconciled["extra"]["tool"] == "github.update_pr"
    assert reconciled["extra"]["project_effect_id"] == effect_id
    assert reconciled["extra"]["project_permission_grant_id"] == "grant-1"
    assert reconciled["extra"]["project_effect_status"] == "succeeded"
    assert reconciled["extra"]["project_effect_reconciled"] is True


@pytest.mark.parametrize(
    ("preflight_error", "reason_code"),
    [
        ("missing_data", "github_update_pr_preflight_bad_result"),
        ("missing_head_sha", "github_update_pr_head_sha_missing"),
    ],
)
def test_project_update_pr_rejects_malformed_preflight_as_invalid_response(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
    preflight_error: str,
    reason_code: str,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    manager = _project_manager(tmp_path)
    provider = _ProjectGithubProvider()
    provider.preflight_error = preflight_error
    register_provider(provider)

    result = _adapter(tmp_path, manager).execute(
        command=_command(), session_id="session-1", trace_id="turn-1"
    )

    assert result["error"]["code"] == "INVALID_RESPONSE"
    assert result["error"]["details"]["reason_code"] == reason_code
    assert provider.mutation_calls == 0


def test_uncertain_update_not_applied_is_not_repeated(
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
        command=command, session_id="session-1", trace_id="turn-1"
    )
    provider.return_uncertain = False

    second = _adapter(tmp_path, manager).execute(
        command=command, session_id="session-1", trace_id="turn-2"
    )

    assert second["error"]["code"] == "UPSTREAM_ERROR"
    assert second["error"]["details"]["reason_code"] == (
        "github_update_pr_readback_not_applied"
    )
    assert provider.mutation_calls == 1


def test_known_update_error_is_public_and_terminal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    manager = _project_manager(tmp_path)
    _issue_grant(manager)
    provider = _ProjectGithubProvider()
    provider.return_error = True
    register_provider(provider)

    result = _adapter(tmp_path, manager).execute(
        command=_command(), session_id="session-1", trace_id="turn-1"
    )

    assert result["error"]["code"] == "UPSTREAM_ERROR"
    assert result["error"]["details"]["provider_error_code"] == "REMOTE_ERROR"
    effect = load_project_effect_record(
        manager,
        task_id="task-1",
        effect_id="effect:github.update_pr:update-pr-1",
    )
    assert effect is not None and effect.status == ProjectEffectStatus.FAILED


def test_malformed_success_does_not_create_a_success_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    manager = _project_manager(tmp_path)
    _issue_grant(manager)
    provider = _ProjectGithubProvider()
    provider.return_malformed_success = True
    register_provider(provider)

    result = _adapter(tmp_path, manager).execute(
        command=_command(), session_id="session-1", trace_id="turn-1"
    )

    effect_id = "effect:github.update_pr:update-pr-1"
    assert result["error"]["code"] == "INVALID_RESPONSE"
    assert result["error"]["details"]["reason_code"] == (
        "github_update_pr_result_invalid"
    )
    effect = load_project_effect_record(
        manager, task_id="task-1", effect_id=effect_id
    )
    assert effect is not None and effect.status == ProjectEffectStatus.FAILED
    assert load_project_effect_receipt(
        manager, task_id="task-1", effect_id=effect_id
    ) is None


def test_non_project_update_pr_keeps_confirmation_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    manager = _project_manager(tmp_path)
    provider = _ProjectGithubProvider()
    register_provider(provider)

    result = _adapter(tmp_path, manager).execute(
        command={"tool_name": "github.update_pr", "args": dict(_ARGS)},
        session_id="session-1",
        trace_id="turn-1",
    )

    assert result["status"] == "needs_user"
    assert result["error"]["code"] == "CONFIRM_REQUIRED"
    assert provider.mutation_calls == 0
