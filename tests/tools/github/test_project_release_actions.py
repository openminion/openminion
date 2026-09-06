from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from openminion.modules.brain.adapters.tool import ToolAdapter
from openminion.modules.brain.adapters.tool.github_release import (
    github_release_action_scope,
)
from openminion.modules.brain.adapters.tool.github_workflow import (
    github_workflow_action_scope,
)
from openminion.modules.task import (
    AutonomyRunPhase,
    AutonomyRunStatus,
    TaskManager,
    build_autonomy_run,
    build_project_run_projection,
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


_WORKFLOW_ARGS = {
    "owner": "openminion",
    "repo": "release-test",
    "workflow": "release.yml",
    "ref": "v1.2.3-rc1",
    "request_id": "release-123",
    "target": "testpypi",
    "inputs": {"request_id": "release-123", "target": "testpypi"},
}
_RELEASE_ARGS = {
    "owner": "openminion",
    "repo": "release-test",
    "tag": "v1.2.3-rc1",
    "expected_commit_sha": "a" * 40,
    "title": "v1.2.3-rc1",
    "notes": "RC notes",
    "draft": True,
    "prerelease": True,
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
                "operation": operation,
                **kwargs,
            }
        )


class _Provider:
    provider_id = DEFAULT_GITHUB_PROVIDER_ID

    def __init__(self) -> None:
        self.workflow_match = "exact"
        self.workflow_mutations = 0
        self.workflow_status_code: int | None = None
        self.workflow_data_overrides: dict[str, Any] = {}
        self.workflow_run_overrides: dict[str, Any] = {}
        self.release: dict[str, Any] | None = None
        self.release_mutations = 0
        self.release_status_code: int | None = None
        self.release_preflight_overrides: dict[str, Any] = {}
        self.release_result_overrides: dict[str, Any] = {}
        self.tag_sha = "a" * 40

    def _workflow_result(self, args: Mapping[str, Any]) -> dict[str, Any]:
        runs = []
        if self.workflow_match in {"exact", "ambiguous"}:
            count = 1 if self.workflow_match == "exact" else 2
            runs = []
            for index in range(count):
                run = {
                    "run_id": index + 10,
                    "workflow_id": 7,
                    "request_id": args["request_id"],
                    "head_branch": args["ref"],
                    "head_sha": "a" * 40,
                    "event": "workflow_dispatch",
                    "status": "queued",
                    "conclusion": "",
                    "html_url": f"https://github.com/o/r/actions/runs/{index + 10}",
                }
                run.update(self.workflow_run_overrides)
                runs.append(run)
        data = {
            "owner": args["owner"],
            "repo": args["repo"],
            "workflow": args["workflow"],
            "ref": args["ref"],
            "request_id": args["request_id"],
            "target": args.get("target", ""),
            "match": self.workflow_match,
            "runs": runs,
            "truncated": False,
        }
        data.update(self.workflow_data_overrides)
        return {
            "ok": True,
            "data": data,
            "source": {"provider_id": self.provider_id},
        }

    def dispatch_workflow(self, *, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        reconciled = getattr(ctx, "github_dispatch_workflow_reconciled_result", None)
        if isinstance(reconciled, Mapping):
            return dict(reconciled)
        self.workflow_mutations += 1
        if self.workflow_status_code is not None:
            raise ToolRuntimeError(
                "UPSTREAM_ERROR",
                "GitHub unavailable",
                {
                    "reason_code": "github_api_error",
                    "status_code": self.workflow_status_code,
                },
            )
        return self._workflow_result(args)

    def read_dispatch_workflow(
        self, *, args: Mapping[str, Any], ctx: Any
    ) -> dict[str, Any]:
        del ctx
        return self._workflow_result(args)

    def list_workflow_runs(
        self, *, args: Mapping[str, Any], ctx: Any
    ) -> dict[str, Any]:
        del ctx
        return self._workflow_result(args)

    def read_release(self, *, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        del ctx
        data = {
            "owner": args["owner"],
            "repo": args["repo"],
            "tag": args["tag"],
            "tag_sha": self.tag_sha,
            "release": self.release,
        }
        data.update(self.release_preflight_overrides)
        return {
            "ok": True,
            "data": data,
            "source": {"provider_id": self.provider_id},
        }

    def create_release(self, *, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        reconciled = getattr(ctx, "github_create_release_reconciled_result", None)
        if isinstance(reconciled, Mapping):
            return dict(reconciled)
        self.release_mutations += 1
        if self.release_status_code is not None:
            raise ToolRuntimeError(
                "UPSTREAM_ERROR",
                "GitHub unavailable",
                {
                    "reason_code": "github_api_error",
                    "status_code": self.release_status_code,
                },
            )
        self.release = {
            "release_id": 17,
            "tag": args["tag"],
            "title": args["title"],
            "notes": args["notes"],
            "draft": args["draft"],
            "prerelease": args["prerelease"],
            "html_url": "https://github.com/o/r/releases/tag/v1.2.3-rc1",
        }
        self.release.update(self.release_result_overrides)
        return self.read_release(args=args, ctx=ctx)


@pytest.fixture(autouse=True)
def _reset_provider_registry() -> None:
    provider_registry().reset()
    yield
    provider_registry().reset()


def _manager(tmp_path: Any) -> TaskManager:
    manager = TaskManager.for_lifecycle_db(db_path=tmp_path / "tasks.db")
    manager.create_task(
        session_id="session-1",
        mode_name="project",
        goal="release",
        agent_id="agent-1",
        task_id="task-1",
    )
    run = build_autonomy_run(
        goal_text="release",
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
            run,
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


def _adapter(
    tmp_path: Any, manager: TaskManager, telemetry: _Telemetry | None = None
) -> ToolAdapter:
    registry = ToolRegistry()
    register(registry)
    return ToolAdapter(
        workspace_root=tmp_path,
        runtime_registry=registry,
        policy={
            "tools": {
                "allow_exact": ["github.dispatch_workflow", "github.create_release"]
            }
        },
        task_manager=manager,
        telemetryctl=telemetry,
        agent_id="agent-1",
        agent_profile=SimpleNamespace(provider_config_overrides={"github": {}}),
    )


def _command(tool: str, args: Mapping[str, Any], command_id: str) -> dict[str, Any]:
    return {
        "tool_name": tool,
        "args": dict(args),
        "idempotency_key": command_id,
        "meta": {"orchestration": {"task_backed_task_id": "task-1"}},
    }


def _grant(manager: TaskManager, tool: str, scope: str) -> None:
    issued = now_ms()
    issue_project_permission_grant(
        manager,
        task_id="task-1",
        grant_id=f"grant-{tool}",
        tool_name=tool,
        scope=scope,
        issued_at_ms=issued,
        expires_at_ms=issued + 60_000,
        max_uses=1,
    )


def _effect(tool: str, scope: str) -> str:
    return f"effect:{tool}:{hashlib.sha256(scope.encode()).hexdigest()}"


def test_approved_workflow_dispatch_persists_receipt_and_telemetry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    manager = _manager(tmp_path)
    scope = github_workflow_action_scope(_WORKFLOW_ARGS)
    _grant(manager, "github.dispatch_workflow", scope)
    provider = _Provider()
    telemetry = _Telemetry()
    register_provider(provider)

    result = _adapter(tmp_path, manager, telemetry).execute(
        command=_command("github.dispatch_workflow", _WORKFLOW_ARGS, "dispatch-1"),
        session_id="session-1",
        trace_id="turn-1",
    )

    assert result["status"] == "success"
    assert provider.workflow_mutations == 1
    effect = load_project_effect_record(
        manager, task_id="task-1", effect_id=_effect("github.dispatch_workflow", scope)
    )
    assert effect is not None and effect.status == ProjectEffectStatus.SUCCEEDED
    assert [row["operation"] for row in telemetry.operations] == ["invoke", "completed"]
    assert telemetry.operations[-1]["session_id"] == "session-1"
    assert telemetry.operations[-1]["turn_id"] == "turn-1"
    assert telemetry.operations[-1]["extra"]["project_effect_reconciled"] is False


def test_pypi_dispatch_requires_exact_final_release_scope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    manager = _manager(tmp_path)
    test_scope = github_workflow_action_scope(_WORKFLOW_ARGS)
    _grant(manager, "github.dispatch_workflow", test_scope)
    provider = _Provider()
    register_provider(provider)
    pypi_args = {
        **_WORKFLOW_ARGS,
        "target": "pypi",
        "inputs": {"request_id": "release-123", "target": "pypi"},
    }
    telemetry = _Telemetry()
    result = _adapter(tmp_path, manager, telemetry).execute(
        command=_command("github.dispatch_workflow", pypi_args, "dispatch-pypi"),
        session_id="session-1",
        trace_id="turn-1",
    )
    assert result["error"]["code"] == "POLICY_DENIED"
    assert provider.workflow_mutations == 0
    assert telemetry.operations[-1]["operation"] == "blocked_by_policy"


def test_pypi_dispatch_accepts_exact_final_release_scope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    manager = _manager(tmp_path)
    args = {
        **_WORKFLOW_ARGS,
        "target": "pypi",
        "inputs": {"request_id": "release-123", "target": "pypi"},
    }
    _grant(manager, "github.dispatch_workflow", github_workflow_action_scope(args))
    provider = _Provider()
    register_provider(provider)
    result = _adapter(tmp_path, manager).execute(
        command=_command("github.dispatch_workflow", args, "dispatch-pypi"),
        session_id="session-1",
        trace_id="turn-1",
    )
    assert result["status"] == "success"
    assert provider.workflow_mutations == 1


@pytest.mark.parametrize(
    ("input_name", "input_value"),
    [("request_id", "different-request"), ("target", "pypi")],
)
def test_workflow_identity_input_mismatch_stops_before_approval_or_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
    input_name: str,
    input_value: str,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    manager = _manager(tmp_path)
    args = {
        **_WORKFLOW_ARGS,
        "inputs": {**_WORKFLOW_ARGS["inputs"], input_name: input_value},
    }
    _grant(manager, "github.dispatch_workflow", github_workflow_action_scope(args))
    provider = _Provider()
    register_provider(provider)

    result = _adapter(tmp_path, manager).execute(
        command=_command("github.dispatch_workflow", args, "dispatch-mismatch"),
        session_id="session-1",
        trace_id="turn-1",
    )

    assert result["error"]["code"] == "INVALID_ARGUMENT"
    assert provider.workflow_mutations == 0
    policy = load_project_policy_state(manager, task_id="task-1")
    assert policy is not None and policy.grants[0].uses == 0
    scope = github_workflow_action_scope(args)
    assert (
        load_project_effect_record(
            manager,
            task_id="task-1",
            effect_id=_effect("github.dispatch_workflow", scope),
        )
        is None
    )


@pytest.mark.parametrize(
    ("location", "field", "value"),
    [
        ("data", "target", "pypi"),
        ("run", "request_id", "different-request"),
        ("run", "head_branch", "different-ref"),
        ("run", "event", "push"),
    ],
)
def test_workflow_rejects_mismatched_exact_provider_receipt_without_repeat(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
    location: str,
    field: str,
    value: str,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    manager = _manager(tmp_path)
    scope = github_workflow_action_scope(_WORKFLOW_ARGS)
    _grant(manager, "github.dispatch_workflow", scope)
    provider = _Provider()
    overrides = (
        provider.workflow_data_overrides
        if location == "data"
        else provider.workflow_run_overrides
    )
    overrides[field] = value
    register_provider(provider)

    for command_id in ("dispatch-1", "dispatch-2"):
        result = _adapter(tmp_path, manager).execute(
            command=_command("github.dispatch_workflow", _WORKFLOW_ARGS, command_id),
            session_id="session-1",
            trace_id=command_id,
        )
        assert result["error"]["details"]["reason_code"] == (
            "github_workflow_result_mismatch"
        )
        assert result["error"]["details"]["project_effect_uncertain"] is True

    effect = load_project_effect_record(
        manager,
        task_id="task-1",
        effect_id=_effect("github.dispatch_workflow", scope),
    )
    assert effect is not None and effect.status == ProjectEffectStatus.STARTED
    assert provider.workflow_mutations == 1


@pytest.mark.parametrize("readback", ["not_found", "ambiguous"])
def test_workflow_uncertainty_never_repeats_dispatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any, readback: str
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    manager = _manager(tmp_path)
    scope = github_workflow_action_scope(_WORKFLOW_ARGS)
    _grant(manager, "github.dispatch_workflow", scope)
    provider = _Provider()
    provider.workflow_match = "not_found"
    register_provider(provider)
    telemetry = _Telemetry()
    first = _adapter(tmp_path, manager, telemetry).execute(
        command=_command("github.dispatch_workflow", _WORKFLOW_ARGS, "dispatch-1"),
        session_id="session-1",
        trace_id="turn-1",
    )
    assert first["outputs"]["data"]["project_effect_status"] == "started"
    assert any(
        row.get("extra", {}).get("error_code") == "PROJECT_EFFECT_UNCERTAIN"
        for row in telemetry.operations
    )
    provider.workflow_match = readback
    second = _adapter(tmp_path, manager).execute(
        command=_command("github.dispatch_workflow", _WORKFLOW_ARGS, "dispatch-2"),
        session_id="session-1",
        trace_id="turn-2",
    )
    assert second["error"]["details"]["project_effect_uncertain"] is True
    assert provider.workflow_mutations == 1


def test_workflow_started_reconciles_after_restart_without_repeat(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    manager = _manager(tmp_path)
    scope = github_workflow_action_scope(_WORKFLOW_ARGS)
    _grant(manager, "github.dispatch_workflow", scope)
    provider = _Provider()
    provider.workflow_match = "not_found"
    register_provider(provider)
    _adapter(tmp_path, manager).execute(
        command=_command("github.dispatch_workflow", _WORKFLOW_ARGS, "dispatch-1"),
        session_id="session-1",
        trace_id="turn-1",
    )
    manager.close()
    provider.workflow_match = "exact"
    restarted = TaskManager.for_lifecycle_db(db_path=tmp_path / "tasks.db")
    telemetry = _Telemetry()
    result = _adapter(tmp_path, restarted, telemetry).execute(
        command=_command("github.dispatch_workflow", _WORKFLOW_ARGS, "dispatch-2"),
        session_id="session-1",
        trace_id="turn-2",
    )
    assert result["outputs"]["data"]["reconciled"] is True
    assert provider.workflow_mutations == 1
    assert telemetry.operations[-1]["extra"]["project_effect_reconciled"] is True


def test_workflow_5xx_reconciles_after_restart_without_repeat(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    manager = _manager(tmp_path)
    scope = github_workflow_action_scope(_WORKFLOW_ARGS)
    _grant(manager, "github.dispatch_workflow", scope)
    provider = _Provider()
    provider.workflow_status_code = 500
    register_provider(provider)

    first = _adapter(tmp_path, manager).execute(
        command=_command("github.dispatch_workflow", _WORKFLOW_ARGS, "dispatch-1"),
        session_id="session-1",
        trace_id="turn-1",
    )
    assert first["error"]["details"]["project_effect_uncertain"] is True
    manager.close()
    provider.workflow_status_code = None
    restarted = TaskManager.for_lifecycle_db(db_path=tmp_path / "tasks.db")
    result = _adapter(tmp_path, restarted).execute(
        command=_command("github.dispatch_workflow", _WORKFLOW_ARGS, "dispatch-2"),
        session_id="session-1",
        trace_id="turn-2",
    )

    assert result["outputs"]["data"]["reconciled"] is True
    assert provider.workflow_mutations == 1


def test_workflow_4xx_is_terminal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    manager = _manager(tmp_path)
    scope = github_workflow_action_scope(_WORKFLOW_ARGS)
    _grant(manager, "github.dispatch_workflow", scope)
    provider = _Provider()
    provider.workflow_status_code = 422
    register_provider(provider)

    result = _adapter(tmp_path, manager).execute(
        command=_command("github.dispatch_workflow", _WORKFLOW_ARGS, "dispatch-1"),
        session_id="session-1",
        trace_id="turn-1",
    )

    assert result["error"]["details"]["project_effect_uncertain"] is False
    effect = load_project_effect_record(
        manager,
        task_id="task-1",
        effect_id=_effect("github.dispatch_workflow", scope),
    )
    assert effect is not None and effect.status == ProjectEffectStatus.FAILED


def test_release_rejects_tag_sha_mismatch_before_mutation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    manager = _manager(tmp_path)
    provider = _Provider()
    provider.tag_sha = "b" * 40
    register_provider(provider)
    result = _adapter(tmp_path, manager).execute(
        command=_command("github.create_release", _RELEASE_ARGS, "release-1"),
        session_id="session-1",
        trace_id="turn-1",
    )
    assert (
        result["error"]["details"]["reason_code"] == "github_release_tag_sha_mismatch"
    )
    assert provider.release_mutations == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [("owner", "other"), ("repo", "other"), ("tag", "v9.9.9")],
)
def test_release_rejects_mismatched_preflight_identity_before_effect(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
    field: str,
    value: str,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    manager = _manager(tmp_path)
    scope = github_release_action_scope(_RELEASE_ARGS)
    _grant(manager, "github.create_release", scope)
    provider = _Provider()
    provider.release_preflight_overrides[field] = value
    register_provider(provider)

    result = _adapter(tmp_path, manager).execute(
        command=_command("github.create_release", _RELEASE_ARGS, "release-1"),
        session_id="session-1",
        trace_id="turn-1",
    )

    assert result["error"]["details"]["reason_code"] == (
        "github_release_result_mismatch"
    )
    assert provider.release_mutations == 0
    assert (
        load_project_effect_record(
            manager,
            task_id="task-1",
            effect_id=_effect("github.create_release", scope),
        )
        is None
    )
    policy = load_project_policy_state(manager, task_id="task-1")
    assert policy is not None and policy.grants[0].uses == 0


def test_approved_draft_prerelease_persists_exact_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    manager = _manager(tmp_path)
    scope = github_release_action_scope(_RELEASE_ARGS)
    _grant(manager, "github.create_release", scope)
    provider = _Provider()
    telemetry = _Telemetry()
    register_provider(provider)
    result = _adapter(tmp_path, manager, telemetry).execute(
        command=_command("github.create_release", _RELEASE_ARGS, "release-1"),
        session_id="session-1",
        trace_id="turn-1",
    )
    assert result["status"] == "success"
    assert result["outputs"]["data"]["release"]["draft"] is True
    assert result["outputs"]["data"]["release"]["prerelease"] is True
    assert provider.release_mutations == 1
    effect = load_project_effect_record(
        manager, task_id="task-1", effect_id=_effect("github.create_release", scope)
    )
    assert effect is not None and effect.status == ProjectEffectStatus.SUCCEEDED
    assert telemetry.operations[-1]["session_id"] == "session-1"
    assert telemetry.operations[-1]["turn_id"] == "turn-1"


def test_release_rejects_non_boolean_custom_provider_flags(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    manager = _manager(tmp_path)
    scope = github_release_action_scope(_RELEASE_ARGS)
    _grant(manager, "github.create_release", scope)
    provider = _Provider()
    provider.release_result_overrides = {"draft": "false", "prerelease": "false"}
    register_provider(provider)

    result = _adapter(tmp_path, manager).execute(
        command=_command("github.create_release", _RELEASE_ARGS, "release-1"),
        session_id="session-1",
        trace_id="turn-1",
    )

    assert result["error"]["code"] == "INVALID_RESPONSE"
    assert provider.release_mutations == 1
    effect = load_project_effect_record(
        manager,
        task_id="task-1",
        effect_id=_effect("github.create_release", scope),
    )
    assert effect is not None and effect.status == ProjectEffectStatus.STARTED


def test_project_release_denies_without_exact_approval(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    manager = _manager(tmp_path)
    provider = _Provider()
    register_provider(provider)
    telemetry = _Telemetry()
    result = _adapter(tmp_path, manager, telemetry).execute(
        command=_command("github.create_release", _RELEASE_ARGS, "release-1"),
        session_id="session-1",
        trace_id="turn-1",
    )
    assert result["error"]["code"] == "POLICY_DENIED"
    assert provider.release_mutations == 0
    assert telemetry.operations[-1]["operation"] == "blocked_by_policy"


def test_approved_release_and_uncertain_restart_readback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    manager = _manager(tmp_path)
    scope = github_release_action_scope(_RELEASE_ARGS)
    _grant(manager, "github.create_release", scope)
    provider = _Provider()
    provider.release_status_code = 500
    register_provider(provider)
    initial_telemetry = _Telemetry()
    first = _adapter(tmp_path, manager, initial_telemetry).execute(
        command=_command("github.create_release", _RELEASE_ARGS, "release-1"),
        session_id="session-1",
        trace_id="turn-1",
    )
    assert first["error"]["details"]["project_effect_uncertain"] is True
    assert any(
        row.get("extra", {}).get("error_code") == "PROJECT_EFFECT_UNCERTAIN"
        for row in initial_telemetry.operations
    )
    manager.close()
    provider.release_status_code = None
    provider.release = {
        "release_id": 17,
        "tag": _RELEASE_ARGS["tag"],
        "title": _RELEASE_ARGS["title"],
        "notes": _RELEASE_ARGS["notes"],
        "draft": True,
        "prerelease": True,
        "html_url": "https://github.com/o/r/releases/tag/v1.2.3-rc1",
    }
    restarted = TaskManager.for_lifecycle_db(db_path=tmp_path / "tasks.db")
    telemetry = _Telemetry()
    result = _adapter(tmp_path, restarted, telemetry).execute(
        command=_command("github.create_release", _RELEASE_ARGS, "release-2"),
        session_id="session-1",
        trace_id="turn-2",
    )
    assert result["outputs"]["data"]["reconciled"] is True
    assert provider.release_mutations == 1
    assert telemetry.operations[-1]["extra"]["project_effect_reconciled"] is True


def test_release_4xx_is_terminal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    manager = _manager(tmp_path)
    scope = github_release_action_scope(_RELEASE_ARGS)
    _grant(manager, "github.create_release", scope)
    provider = _Provider()
    provider.release_status_code = 422
    register_provider(provider)

    result = _adapter(tmp_path, manager).execute(
        command=_command("github.create_release", _RELEASE_ARGS, "release-1"),
        session_id="session-1",
        trace_id="turn-1",
    )

    assert result["error"]["details"]["project_effect_uncertain"] is False
    effect = load_project_effect_record(
        manager,
        task_id="task-1",
        effect_id=_effect("github.create_release", scope),
    )
    assert effect is not None and effect.status == ProjectEffectStatus.FAILED


def test_release_only_actions_deny_non_project_invocation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    manager = _manager(tmp_path)
    register_provider(_Provider())
    result = _adapter(tmp_path, manager).execute(
        command={"tool_name": "github.create_release", "args": dict(_RELEASE_ARGS)},
        session_id="session-1",
        trace_id="turn-1",
    )
    assert (
        result["error"]["details"]["reason_code"] == "github_release_project_required"
    )
