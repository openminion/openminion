from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from openminion.api.core.profiles import _build_agent_discovery_record
from openminion.base.config import AgentProfileConfig
from openminion.modules.brain.adapters.tool.permission_mode import (
    is_tool_blocked_by_readonly,
)
from openminion.modules.brain.adapters.tool.runtime import ToolAdapter
from openminion.modules.tool import build_default_tool_registry
from openminion.modules.tool.contracts.model_ids import (
    MODEL_AGENT_GET,
    MODEL_AGENT_LIST,
    MODEL_TASK_DELEGATE,
    is_valid_model_tool_id,
)
from openminion.modules.tool.contracts.runtime_ids import (
    RUNTIME_AGENT_GET,
    RUNTIME_AGENT_LIST,
    RUNTIME_TASK_DELEGATE,
    is_valid_runtime_binding_id,
)
from openminion.modules.tool.contracts.display_names import (
    MODEL_TOOL_DISPLAY_NAME_MAP,
    display_name_for_tool_name,
)
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.runtime.context import RuntimeContext
from openminion.modules.task import (
    TaskManager,
    build_autonomy_run,
    build_project_run_projection,
    save_project_run_checkpoint,
)
from openminion.modules.task.project import checkpoints as project_checkpoints
from openminion.tools.agent.plugin import (
    AgentGetArgs,
    AgentListArgs,
    TaskDelegateArgs,
    _h_agent_get,
    _h_agent_list,
    _h_task_delegate,
)
from openminion.tools.agent.registrar import REGISTRAR
from tests.artifact.utils import artifact_ctl


def test_canonical_tool_ids_registered() -> None:
    assert is_valid_model_tool_id(MODEL_AGENT_LIST)
    assert is_valid_model_tool_id(MODEL_AGENT_GET)
    assert is_valid_model_tool_id(MODEL_TASK_DELEGATE)
    assert MODEL_AGENT_LIST == "agent.list"
    assert MODEL_AGENT_GET == "agent.get"
    assert MODEL_TASK_DELEGATE == "task.delegate"


def test_canonical_runtime_binding_ids_registered() -> None:
    assert is_valid_runtime_binding_id(RUNTIME_AGENT_LIST)
    assert is_valid_runtime_binding_id(RUNTIME_AGENT_GET)
    assert is_valid_runtime_binding_id(RUNTIME_TASK_DELEGATE)


def test_display_names_populated() -> None:
    assert MODEL_TOOL_DISPLAY_NAME_MAP[MODEL_AGENT_LIST] == "List Agents"
    assert MODEL_TOOL_DISPLAY_NAME_MAP[MODEL_AGENT_GET] == "Get Agent"
    assert MODEL_TOOL_DISPLAY_NAME_MAP[MODEL_TASK_DELEGATE] == "Delegate Task"
    assert display_name_for_tool_name("runtime.agent.list") == "List Agents"


def test_default_registry_includes_agent_family() -> None:
    registry = build_default_tool_registry()
    tools = set(registry.list().keys())
    assert "agent.list" in tools
    assert "agent.get" in tools
    assert "task.delegate" in tools


def test_task_delegate_manifest_describes_implemented_lifecycle() -> None:
    runtime: Any = SimpleNamespace()
    manifest = REGISTRAR.get_manifest(runtime)
    task_delegate = next(
        item
        for item in manifest.model_tools
        if item.model_tool_id == MODEL_TASK_DELEGATE
    )

    description = task_delegate.description.lower()
    assert "pending" not in description
    assert "not_implemented" not in description
    assert "sync" in description
    assert "async" in description
    assert "cancel" in description


def test_task_delegate_schema_makes_lifecycle_order_explicit() -> None:
    mode_description = TaskDelegateArgs.model_fields["mode"].description or ""
    artifact_description = (
        TaskDelegateArgs.model_fields["child_artifact"].description or ""
    )

    assert "Use sync or async to start child work" in mode_description
    assert "There is no create mode" in mode_description
    assert "required for review/accept/reject" in artifact_description
    agent_description = TaskDelegateArgs.model_fields["agent_id"].description or ""
    assert "Call agent.list first" in agent_description
    assert "never invent" in agent_description


def test_readonly_blocks_task_delegate() -> None:
    assert is_tool_blocked_by_readonly("task.delegate")
    assert not is_tool_blocked_by_readonly("agent.list")
    assert not is_tool_blocked_by_readonly("agent.get")


def test_agent_list_defaults() -> None:
    args = AgentListArgs()
    assert args.status == ""
    assert args.limit == 50


def test_agent_list_limit_clamped_by_validator() -> None:
    with pytest.raises(Exception):
        AgentListArgs(limit=0)
    with pytest.raises(Exception):
        AgentListArgs(limit=500)


def test_agent_get_requires_agent_id() -> None:
    with pytest.raises(Exception):
        AgentGetArgs(agent_id="")  # type: ignore[call-arg]


def test_task_delegate_requires_fields() -> None:
    with pytest.raises(Exception):
        TaskDelegateArgs(agent_id="", instruction="hi")  # type: ignore[call-arg]
    with pytest.raises(Exception):
        TaskDelegateArgs(agent_id="x", instruction="")  # type: ignore[call-arg]


def test_task_delegate_timeout_bounds() -> None:
    with pytest.raises(Exception):
        TaskDelegateArgs(agent_id="x", instruction="y", timeout_seconds=0)
    with pytest.raises(Exception):
        TaskDelegateArgs(agent_id="x", instruction="y", timeout_seconds=99_999)


def test_code_bearing_delegate_requires_sync_mode() -> None:
    with pytest.raises(Exception):
        TaskDelegateArgs(
            mode="async",
            agent_id="x",
            instruction="edit files",
            code_bearing=True,
        )


def _ctx_without_storage() -> SimpleNamespace:
    return SimpleNamespace(
        policy=SimpleNamespace(raw={}),
        env={},
    )


def _ctx_with_agent_query(agents: list[dict[str, Any]]) -> SimpleNamespace:
    return SimpleNamespace(
        policy=SimpleNamespace(raw={}),
        env={},
        agent_query=lambda: list(agents),
    )


def _ctx_with_registry(records: list[Any]) -> tuple[SimpleNamespace, dict[str, Any]]:
    calls: dict[str, Any] = {"list_called_with": None, "get_called_with": None}

    class _StubRegistry:
        def list_agents(self, *, status: str | None = None):
            calls["list_called_with"] = status
            return list(records)

        def get_agent(self, agent_id: str):
            calls["get_called_with"] = agent_id
            for r in records:
                if getattr(r, "agent_id", "") == agent_id:
                    return r
            return None

    ctx = SimpleNamespace(
        policy=SimpleNamespace(raw={"storage_path": "/tmp/fake.db"}),
        env={},
        _stub_registry=_StubRegistry(),
    )
    return ctx, calls


def test_agent_list_degrades_when_storage_unconfigured() -> None:
    ctx = _ctx_without_storage()
    out = _h_agent_list({}, ctx)  # type: ignore[arg-type]
    assert out["ok"] is True
    assert out["agents"] == []
    assert out["count"] == 0
    assert out["storage_unavailable"] is True


def test_agent_get_raises_when_storage_unconfigured() -> None:
    ctx = _ctx_without_storage()
    with pytest.raises(ToolRuntimeError) as exc_info:
        _h_agent_get({"agent_id": "alpha"}, ctx)  # type: ignore[arg-type]
    assert exc_info.value.code == "DEPENDENCY_MISSING"
    assert exc_info.value.details["reason_code"] == "agent_registry_unconfigured"


def test_agent_list_prefers_runtime_discovery_snapshot() -> None:
    ctx = _ctx_with_agent_query(
        [
            {
                "agent_id": "configured-cold",
                "state": "configured",
                "configured": True,
                "hot": False,
                "registry_present": False,
            },
            {
                "agent_id": "heartbeat-active",
                "state": "running",
                "configured": False,
                "hot": False,
                "heartbeat_active": True,
            },
        ]
    )

    out = _h_agent_list({"status": "running", "limit": 10}, ctx)  # type: ignore[arg-type]

    assert out["source"] == "runtime_agent_discovery"
    assert out["count"] == 1
    assert out["agents"][0]["agent_id"] == "heartbeat-active"
    assert out["agents"][0]["heartbeat_active"] is True


def test_agent_discovery_exposes_configured_role_and_skills() -> None:
    record = _build_agent_discovery_record(
        agent_id="researcher",
        configured_profile=AgentProfileConfig(
            name="Researcher",
            role="evidence auditor",
            skill=["web-research", "source-review"],
            skill_catalog=["source-review", "security-scan"],
        ),
        registry_record=None,
        heartbeat_record=None,
        hot=False,
    )

    payload = record.as_payload()
    assert payload["role"] == "evidence auditor"
    assert payload["skills"] == [
        "web-research",
        "source-review",
        "security-scan",
    ]
    assert payload["capabilities"] == ["delegate.sync"]


def test_brain_tool_adapter_threads_runtime_agent_discovery(tmp_path: Path) -> None:
    adapter = ToolAdapter(
        workspace_root=tmp_path,
        agent_query=lambda: [
            {
                "agent_id": "researcher",
                "display_name": "Researcher",
                "role": "evidence researcher",
                "skills": ["source-review"],
                "state": "configured",
                "configured": True,
            }
        ],
    )

    result = adapter.execute(
        command={"tool_name": "agent.list", "args": {}},
        session_id="session-agent-discovery",
        trace_id="trace-agent-discovery",
    )

    assert result["status"] == "success"
    assert result["outputs"]["agents"][0]["role"] == "evidence researcher"
    assert result["outputs"]["agents"][0]["skills"] == ["source-review"]


def test_agent_get_prefers_runtime_discovery_snapshot() -> None:
    ctx = _ctx_with_agent_query(
        [
            {
                "agent_id": "hot-agent",
                "state": "running",
                "configured": True,
                "hot": True,
                "registry_present": True,
            }
        ]
    )

    out = _h_agent_get({"agent_id": "hot-agent"}, ctx)  # type: ignore[arg-type]

    assert out["source"] == "runtime_agent_discovery"
    assert out["agent"]["agent_id"] == "hot-agent"
    assert out["agent"]["hot"] is True


def test_agent_get_runtime_snapshot_not_found_does_not_fall_back_to_storage() -> None:
    ctx = _ctx_with_agent_query([])

    with pytest.raises(ToolRuntimeError) as exc_info:
        _h_agent_get({"agent_id": "registry-only"}, ctx)  # type: ignore[arg-type]

    assert exc_info.value.code == "NOT_FOUND"
    assert exc_info.value.details["reason_code"] == "agent_not_found"


def test_agent_list_returns_records_from_registry(monkeypatch) -> None:
    record = SimpleNamespace(
        agent_id="alpha",
        display_name="Alpha",
        description="primary agent",
        config_path="/p",
        workspace_root="/w",
        tags=["a", "b"],
        status="registered",
        registered_at="2026-05-30",
        updated_at="2026-05-30",
    )
    ctx, calls = _ctx_with_registry([record])

    import openminion.tools.agent.plugin as plugin_mod

    monkeypatch.setattr(
        plugin_mod, "_resolve_agent_registry", lambda c: c._stub_registry
    )
    out = _h_agent_list({"status": "registered", "limit": 10}, ctx)  # type: ignore[arg-type]
    assert out["ok"] is True
    assert out["source"] == "registry_compatibility_fallback"
    assert out["count"] == 1
    assert out["agents"][0]["agent_id"] == "alpha"
    assert out["agents"][0]["tags"] == ["a", "b"]
    assert out["agents"][0]["registry_present"] is True
    assert out["agents"][0]["configured"] is False
    assert out["agents"][0]["running"] is False
    assert out["agents"][0]["state"] == "registered"
    assert out["agents"][0]["capabilities"] == ["delegate.sync"]
    assert calls["list_called_with"] == "registered"


def test_agent_get_returns_record(monkeypatch) -> None:
    record = SimpleNamespace(
        agent_id="alpha",
        display_name="Alpha",
        description="",
        config_path="",
        workspace_root="",
        tags=[],
        status="registered",
        registered_at="",
        updated_at="",
    )
    ctx, calls = _ctx_with_registry([record])
    import openminion.tools.agent.plugin as plugin_mod

    monkeypatch.setattr(
        plugin_mod, "_resolve_agent_registry", lambda c: c._stub_registry
    )
    out = _h_agent_get({"agent_id": "alpha"}, ctx)  # type: ignore[arg-type]
    assert out["ok"] is True
    assert out["source"] == "registry_compatibility_fallback"
    assert out["agent"]["agent_id"] == "alpha"
    assert calls["get_called_with"] == "alpha"


def test_agent_get_not_found(monkeypatch) -> None:
    ctx, _ = _ctx_with_registry([])
    import openminion.tools.agent.plugin as plugin_mod

    monkeypatch.setattr(
        plugin_mod, "_resolve_agent_registry", lambda c: c._stub_registry
    )
    with pytest.raises(ToolRuntimeError) as exc_info:
        _h_agent_get({"agent_id": "ghost"}, ctx)  # type: ignore[arg-type]
    assert exc_info.value.code == "NOT_FOUND"
    assert exc_info.value.details["reason_code"] == "agent_not_found"
    assert exc_info.value.details["agent_id"] == "ghost"


def test_task_delegate_seam_unavailable_returns_typed_error() -> None:
    ctx = _ctx_without_storage()  # SimpleNamespace has no a2a_delegate_api
    with pytest.raises(ToolRuntimeError) as exc_info:
        _h_task_delegate(
            {
                "agent_id": "beta",
                "instruction": "ship the change",
                "timeout_seconds": 60,
            },
            ctx,  # type: ignore[arg-type]
        )
    assert exc_info.value.code == "DEPENDENCY_MISSING"
    details = exc_info.value.details
    assert details["reason_code"] == "task_delegate_seam_unavailable"
    assert details["agent_id"] == "beta"


def _ctx_with_seam(seam: Any) -> SimpleNamespace:
    return SimpleNamespace(
        policy=SimpleNamespace(raw={}),
        env={},
        a2a_delegate_api=seam,
        workspace=Path("/workspace"),
        session_id="session-from-context",
    )


def test_task_delegate_happy_path_maps_seam_result() -> None:
    from openminion.modules.tool.runtime.delegation import A2ADelegateResult

    calls: dict[str, Any] = {}

    class _Seam:
        def delegate(
            self,
            *,
            agent_id,
            instruction,
            timeout_seconds,
            permission_mode="ask",
            workspace_root="",
            cwd="",
        ):
            calls.update(
                agent_id=agent_id,
                instruction=instruction,
                timeout_seconds=timeout_seconds,
                permission_mode=permission_mode,
                workspace_root=workspace_root,
                cwd=cwd,
            )
            return A2ADelegateResult(
                ok=True,
                status="success",
                content="done",
                target_agent_id=agent_id,
                trace_id="t-1",
                task_id="task-1",
                outputs={"k": "v"},
            )

    out = _h_task_delegate(
        {"agent_id": "beta", "instruction": "ship it", "timeout_seconds": 45},
        _ctx_with_seam(_Seam()),  # type: ignore[arg-type]
    )
    assert out["ok"] is True
    assert out["agent_id"] == "beta"
    assert out["status"] == "success"
    assert out["content"] == "done"
    assert out["outputs"] == {"k": "v"}
    assert out["trace_id"] == "t-1"
    assert out["task_id"] == "task-1"
    assert calls == {
        "agent_id": "beta",
        "instruction": "ship it",
        "timeout_seconds": 45,
        "permission_mode": "ask",
        "workspace_root": "/workspace",
        "cwd": "/workspace",
    }


def test_task_delegate_can_narrow_child_permission_to_readonly() -> None:
    from openminion.modules.tool.runtime.delegation import A2ADelegateResult

    calls: dict[str, Any] = {}

    class _Seam:
        def delegate(self, **kwargs: Any) -> A2ADelegateResult:
            calls.update(kwargs)
            return A2ADelegateResult(ok=True, status="success", content="research")

    context = _ctx_with_seam(_Seam())
    context.permission_mode = "ask"
    out = _h_task_delegate(
        {
            "agent_id": "bogr-readonly-researcher",
            "instruction": "find candidate contracts",
            "child_permission_mode": "readonly",
        },
        context,  # type: ignore[arg-type]
    )

    assert out["content"] == "research"
    assert calls["permission_mode"] == "readonly"


@pytest.mark.parametrize(
    "arguments",
    [
        {
            "mode": "status",
            "task_id": "task-1",
            "child_permission_mode": "readonly",
        },
        {
            "agent_id": "worker",
            "instruction": "edit code",
            "code_bearing": True,
            "child_permission_mode": "readonly",
        },
        {
            "agent_id": "worker",
            "instruction": "research",
            "child_permission_mode": "ask",
        },
    ],
)
def test_task_delegate_rejects_incompatible_child_permission_modes(
    arguments: dict[str, Any],
) -> None:
    from openminion.tools.agent.plugin import TaskDelegateArgs

    with pytest.raises(ValueError, match="child_permission_mode"):
        TaskDelegateArgs.model_validate(arguments)


def test_tool_adapter_binds_code_delegate_to_project_worktree_and_verifier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openminion.modules.brain.execution.worktree_children import (
        load_child_worktree_record,
    )
    from openminion.modules.tool.runtime.delegation import A2ADelegateResult
    from openminion.modules.task.project.verification import (
        run_project_verification_commands,
    )

    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "value.py").write_text("VALUE = 0\n", encoding="utf-8")
    (repository / "verify.py").write_text(
        "from value import VALUE\nassert VALUE == 1\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "user.name=Test",
            "commit",
            "-qm",
            "seed",
        ],
        cwd=repository,
        check=True,
    )
    manager = TaskManager.for_lifecycle_db(db_path=tmp_path / "task.db")
    run = build_autonomy_run(
        goal_text="Set VALUE to 1",
        goal_id="goal-1",
        session_id="session-1",
        workspace_ref=f"local:{repository}",
        max_iterations=2,
        verification_commands=(f"{sys.executable} verify.py",),
        verification_timeout_seconds=17,
    ).model_copy(update={"task_id": "task-1"})
    manager.create_task(
        session_id=run.session_id,
        mode_name="project",
        goal=run.goal_text,
        agent_id="parent",
        task_id="task-1",
    )
    project_run = build_project_run_projection(
        run,
        objective_ledger_ref="objective",
        evidence_ledger_ref="evidence",
        resume_packet_ref="resume",
        operator_decision_log_ref="operator-decisions",
        capability_plan_ref="capabilities",
        metrics_summary_ref="metrics",
    )
    save_project_run_checkpoint(
        manager,
        project_run,
        checkpoint_id="checkpoint-1",
        payload=project_checkpoints.initial_repository_lifecycle_payload(
            run,
            project_run,
            launch_approved=True,
            verification_commands=run.execution_selectors.verification_commands,
        ),
    )

    delegate_timeouts: list[int] = []
    verifier_timeouts: list[int] = []

    def run_verifier(commands, *, workspace, timeout_seconds):  # noqa: ANN001
        verifier_timeouts.append(timeout_seconds)
        return run_project_verification_commands(
            commands,
            workspace=workspace,
            timeout_seconds=timeout_seconds,
        )

    monkeypatch.setattr(
        "openminion.tools.agent.plugin.run_project_verification_commands",
        run_verifier,
    )

    class _Seam:
        def delegate(self, **kwargs):
            delegate_timeouts.append(kwargs["timeout_seconds"])
            child = Path(kwargs["workspace_root"])
            (child / "value.py").write_text("VALUE = 1\n", encoding="utf-8")
            return A2ADelegateResult(
                ok=True,
                status="success",
                content="implemented",
                target_agent_id="implementer",
                trace_id="delegate-trace",
            )

        def review_readonly(self, **kwargs):
            return A2ADelegateResult(
                ok=True,
                status="success",
                content="approved",
                target_agent_id=kwargs["reviewer_agent_id"],
                outputs={
                    "review_receipt": {
                        "passed": True,
                        "reviewer_agent_id": kwargs["reviewer_agent_id"],
                        "target_digest": kwargs["target_digest"],
                        "bundle_ref": kwargs["bundle_ref"],
                        "verifier_refs": kwargs["verifier_refs"],
                    }
                },
            )

    with artifact_ctl(tmp_path) as ctl:
        adapter = ToolAdapter(
            workspace_root=tmp_path,
            artifactctl=ctl,
            a2a_delegate_api=_Seam(),
            agent_query=lambda: [
                {"agent_id": "implementer"},
                {"agent_id": "reviewer"},
            ],
            task_manager=manager,
            agent_id="parent",
        )
        result = adapter.execute(
            command={
                "tool_name": "task.delegate",
                "command_id": "delegate-code",
                "args": {
                    "mode": "sync",
                    "agent_id": "implementer",
                    "instruction": "Set VALUE to 1.",
                    "code_bearing": True,
                    "timeout_seconds": 31,
                },
                "inputs": {"permission_mode": "bypass"},
                "meta": {"orchestration": {"task_backed_task_id": "task-1"}},
            },
            session_id="session-1",
            trace_id="turn-1",
        )
        assert result["status"] == "success", result
        artifact = result["outputs"]["child_artifact"]
        review = adapter.execute(
            command={
                "tool_name": "task.delegate",
                "command_id": "review-code",
                "args": {
                    "mode": "review",
                    "agent_id": "reviewer",
                    "instruction": "Review the exact child change.",
                    "child_artifact": artifact,
                    "review_criteria": ["VALUE is set to 1"],
                },
                "inputs": {"permission_mode": "bypass"},
            },
            session_id="session-1",
            trace_id="turn-2",
        )
        assert review["status"] == "success", review
        accepted = adapter.execute(
            command={
                "tool_name": "task.delegate",
                "command_id": "accept-code",
                "args": {"mode": "accept", "child_artifact": artifact},
                "inputs": {"permission_mode": "bypass"},
            },
            session_id="session-1",
            trace_id="turn-3",
        )
        assert accepted["status"] == "success", accepted
        record = load_child_worktree_record(ctl, artifact["record_alias"])

    manager.close()
    assert record["validation"]["passed"] is True
    assert record["integration_status"] == "accepted"
    assert record["cleaned_up"] is True
    assert (repository / "value.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert delegate_timeouts == [31]
    assert verifier_timeouts == [17]


def test_code_bearing_delegate_releases_worktree_when_delegate_raises(
    tmp_path: Path, monkeypatch
) -> None:
    released = []
    lease = SimpleNamespace(
        worktree=tmp_path / "child",
        isolator=SimpleNamespace(release=lambda: released.append(True)),
    )
    checkpoint = SimpleNamespace(
        project_run=SimpleNamespace(
            workspace_ref=f"local:{tmp_path}",
            execution_selectors=SimpleNamespace(
                verification_commands=(f"{sys.executable} verify.py",),
                verification_timeout_seconds=17,
            ),
        )
    )
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(
        "openminion.tools.agent.plugin.allocate_delegated_worktree",
        lambda **_kwargs: lease,
    )
    monkeypatch.setattr(
        "openminion.tools.agent.plugin.load_latest_project_checkpoint",
        lambda *_args, **_kwargs: checkpoint,
    )
    monkeypatch.setattr(
        "openminion.tools.agent.plugin.repository_project_launch_approved",
        lambda _checkpoint: True,
    )

    class _Seam:
        def delegate(self, **_kwargs):
            raise RuntimeError("delegate crashed")

    with pytest.raises(RuntimeError, match="delegate crashed"):
        _h_task_delegate(
            {
                "mode": "sync",
                "agent_id": "implementer",
                "instruction": "Edit files.",
                "code_bearing": True,
            },
            cast(
                RuntimeContext,
                SimpleNamespace(
                    a2a_delegate_api=_Seam(),
                    artifactctl=object(),
                    policy=SimpleNamespace(raw={}),
                    workspace=tmp_path,
                    task_manager=object(),
                    project_task_id="task-1",
                ),
            ),
        )

    assert released == [True]


def test_code_bearing_delegate_requires_project_artifact_owner_and_verifier(
    tmp_path: Path, monkeypatch
) -> None:
    class _Seam:
        def delegate(self, **_kwargs):
            raise AssertionError("delegation must not start")

    args = {
        "mode": "sync",
        "agent_id": "implementer",
        "instruction": "Edit files.",
        "code_bearing": True,
    }
    with pytest.raises(ToolRuntimeError) as missing_artifact:
        _h_task_delegate(
            args,
            cast(
                RuntimeContext,
                SimpleNamespace(
                    a2a_delegate_api=_Seam(),
                    artifactctl=None,
                    policy=SimpleNamespace(raw={}),
                ),
            ),
        )
    assert missing_artifact.value.details["reason_code"] == "artifactctl_unavailable"

    with pytest.raises(ToolRuntimeError) as missing_project:
        _h_task_delegate(
            args,
            cast(
                RuntimeContext,
                SimpleNamespace(
                    a2a_delegate_api=_Seam(),
                    artifactctl=object(),
                    policy=SimpleNamespace(raw={}),
                    task_manager=None,
                    project_task_id="",
                ),
            ),
        )
    assert missing_project.value.details["reason_code"] == "project_context_unavailable"

    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(
        "openminion.tools.agent.plugin.load_latest_project_checkpoint",
        lambda *_args, **_kwargs: SimpleNamespace(
            project_run=SimpleNamespace(
                workspace_ref=f"local:{tmp_path}",
                execution_selectors=SimpleNamespace(verification_commands=()),
            )
        ),
    )
    monkeypatch.setattr(
        "openminion.tools.agent.plugin.repository_project_launch_approved",
        lambda _checkpoint: True,
    )
    with pytest.raises(ToolRuntimeError) as missing_verifier:
        _h_task_delegate(
            args,
            cast(
                RuntimeContext,
                SimpleNamespace(
                    a2a_delegate_api=_Seam(),
                    artifactctl=object(),
                    policy=SimpleNamespace(raw={}),
                    task_manager=object(),
                    project_task_id="task-1",
                ),
            ),
        )
    assert (
        missing_verifier.value.details["reason_code"] == "project_verifier_unavailable"
    )


def test_code_bearing_delegate_requires_approved_project_launch(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(
        "openminion.tools.agent.plugin.load_latest_project_checkpoint",
        lambda *_args, **_kwargs: SimpleNamespace(
            project_run=SimpleNamespace(workspace_ref=f"local:{tmp_path}")
        ),
    )
    monkeypatch.setattr(
        "openminion.tools.agent.plugin.repository_project_launch_approved",
        lambda _checkpoint: False,
    )
    monkeypatch.setattr(
        "openminion.tools.agent.plugin.allocate_delegated_worktree",
        lambda **_kwargs: pytest.fail("unapproved delegation allocated a worktree"),
    )

    with pytest.raises(ToolRuntimeError) as denied:
        _h_task_delegate(
            {
                "mode": "sync",
                "agent_id": "implementer",
                "instruction": "Edit files.",
                "code_bearing": True,
            },
            cast(
                RuntimeContext,
                SimpleNamespace(
                    a2a_delegate_api=object(),
                    artifactctl=object(),
                    task_manager=object(),
                    project_task_id="task-1",
                ),
            ),
        )

    assert denied.value.details["reason_code"] == "project_launch_unapproved"


def test_task_delegate_async_mode_returns_task_handle() -> None:
    from openminion.modules.tool.runtime.delegation import A2ADelegateResult

    calls: dict[str, Any] = {}

    class _Seam:
        def delegate(
            self,
            *,
            agent_id,
            instruction,
            timeout_seconds,
            mode="sync",
            permission_mode="ask",
            workspace_root="",
            cwd="",
        ):
            calls.update(
                agent_id=agent_id,
                instruction=instruction,
                timeout_seconds=timeout_seconds,
                mode=mode,
                permission_mode=permission_mode,
                workspace_root=workspace_root,
                cwd=cwd,
            )
            return A2ADelegateResult(
                ok=True,
                status="running",
                content="started",
                target_agent_id=agent_id,
                trace_id="t-async",
                task_id="job-1",
            )

    out = _h_task_delegate(
        {
            "mode": "async",
            "agent_id": "beta",
            "instruction": "ship it later",
            "timeout_seconds": 45,
        },
        _ctx_with_seam(_Seam()),  # type: ignore[arg-type]
    )
    assert out["ok"] is True
    assert out["mode"] == "async"
    assert out["status"] == "running"
    assert out["task_id"] == "job-1"
    assert calls == {
        "agent_id": "beta",
        "instruction": "ship it later",
        "timeout_seconds": 45,
        "mode": "async",
        "permission_mode": "ask",
        "workspace_root": "/workspace",
        "cwd": "/workspace",
    }


def test_task_delegate_list_status_result_resume_and_cancel_use_lifecycle_methods() -> (
    None
):
    from openminion.modules.tool.runtime.delegation import A2ADelegateResult

    calls: list[tuple[str, str]] = []
    bindings: list[dict[str, str]] = []

    class _Seam:
        def bind_observability(self, **kwargs: str) -> None:
            bindings.append(dict(kwargs))

        def list_recent(self, *, limit):
            calls.append(("list", str(limit)))
            return A2ADelegateResult(
                ok=True,
                status="success",
                outputs={"jobs": [], "count": 0, "limit": limit},
            )

        def status(self, *, task_id):
            calls.append(("status", task_id))
            return A2ADelegateResult(ok=True, status="running", task_id=task_id)

        def resume(self, *, task_id):
            calls.append(("resume", task_id))
            return A2ADelegateResult(ok=True, status="running", task_id=task_id)

        def cancel(self, *, task_id):
            calls.append(("cancel", task_id))
            return A2ADelegateResult(ok=True, status="canceled", task_id=task_id)

    seam = _Seam()

    assert (
        _h_task_delegate(  # type: ignore[arg-type]
            {"mode": "list", "limit": 10}, _ctx_with_seam(seam)
        )["outputs"]["limit"]
        == 10
    )

    assert (
        _h_task_delegate(  # type: ignore[arg-type]
            {"mode": "result", "task_id": "job-1"}, _ctx_with_seam(seam)
        )["status"]
        == "running"
    )
    assert (
        _h_task_delegate(  # type: ignore[arg-type]
            {"mode": "status", "task_id": "job-1"}, _ctx_with_seam(seam)
        )["status"]
        == "running"
    )
    assert (
        _h_task_delegate(  # type: ignore[arg-type]
            {"mode": "resume", "task_id": "job-1"}, _ctx_with_seam(seam)
        )["status"]
        == "running"
    )
    assert (
        _h_task_delegate(  # type: ignore[arg-type]
            {"mode": "cancel", "task_id": "job-1"}, _ctx_with_seam(seam)
        )["status"]
        == "canceled"
    )
    assert calls == [
        ("list", "10"),
        ("status", "job-1"),
        ("status", "job-1"),
        ("resume", "job-1"),
        ("cancel", "job-1"),
    ]
    assert len(bindings) == 5
    assert {binding["session_id"] for binding in bindings} == {"session-from-context"}


def test_task_delegate_reject_requires_durable_record_alias() -> None:
    ctx = cast(
        RuntimeContext,
        SimpleNamespace(policy=SimpleNamespace(raw={}), env={}, artifactctl=object()),
    )

    with pytest.raises(ToolRuntimeError) as exc_info:
        _h_task_delegate(
            {
                "mode": "reject",
                "child_artifact": {
                    "artifact": {"status": "stored", "bundle_ref": "artifact://a"}
                },
            },
            ctx,
        )

    assert exc_info.value.code == "POLICY_DENIED"
    assert exc_info.value.details["reason_code"] == "missing_record_alias"


def test_task_delegate_unknown_target_maps_not_found() -> None:
    from openminion.modules.tool.runtime.delegation import A2ADelegateResult

    class _Seam:
        def delegate(
            self,
            *,
            agent_id,
            instruction,
            timeout_seconds,
            permission_mode="ask",
            workspace_root="",
            cwd="",
        ):
            return A2ADelegateResult(
                ok=False,
                status="failed",
                error_code="AGENT_NOT_FOUND",
                error_message=f"Agent {agent_id!r} is not registered.",
                target_agent_id=agent_id,
            )

    with pytest.raises(ToolRuntimeError) as exc_info:
        _h_task_delegate(
            {"agent_id": "ghost", "instruction": "do x"},
            _ctx_with_seam(_Seam()),  # type: ignore[arg-type]
        )
    assert exc_info.value.code == "NOT_FOUND"
    details = exc_info.value.details
    assert details["reason_code"] == "task_delegate_failed"
    assert details["delegate_error_code"] == "AGENT_NOT_FOUND"
    assert details["target_agent_id"] == "ghost"


def test_task_delegate_rejects_invented_agent_with_visible_exact_ids() -> None:
    class _Seam:
        def delegate(self, **_kwargs):
            raise AssertionError("invalid target must fail before delegation")

    ctx = _ctx_with_seam(_Seam())
    ctx.agent_query = lambda: [
        {"agent_id": "minimax-reviewer", "state": "configured"},
        {"agent_id": "minimax-validator", "state": "configured"},
    ]

    with pytest.raises(ToolRuntimeError) as exc_info:
        _h_task_delegate(
            {"agent_id": "spec-reviewer", "instruction": "review"},
            ctx,  # type: ignore[arg-type]
        )

    assert exc_info.value.code == "NOT_FOUND"
    assert exc_info.value.details["reason_code"] == "agent_not_found"
    assert exc_info.value.details["available_agent_ids"] == [
        "minimax-reviewer",
        "minimax-validator",
    ]


def test_task_delegate_failure_maps_upstream_error() -> None:
    from openminion.modules.tool.runtime.delegation import A2ADelegateResult

    class _Seam:
        def delegate(
            self,
            *,
            agent_id,
            instruction,
            timeout_seconds,
            permission_mode="ask",
            workspace_root="",
            cwd="",
        ):
            return A2ADelegateResult(
                ok=False,
                status="failed",
                error_code="A2A_DELEGATE_FAILED",
                error_message="sub-agent crashed",
                target_agent_id=agent_id,
            )

    with pytest.raises(ToolRuntimeError) as exc_info:
        _h_task_delegate(
            {"agent_id": "beta", "instruction": "do x"},
            _ctx_with_seam(_Seam()),  # type: ignore[arg-type]
        )
    assert exc_info.value.code == "UPSTREAM_ERROR"
    assert exc_info.value.details["delegate_error_code"] == "A2A_DELEGATE_FAILED"
