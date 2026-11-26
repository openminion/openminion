from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

from openminion.modules.brain.adapters.tool.runtime import ToolAdapter
from openminion.modules.brain.tool_catalog.runtime import RunnerToolCatalog
from openminion.modules.brain.loop.services import turn_tool_allowlist
import pytest
from openminion.modules.brain.tools.schema import collect_runtime_tool_names
from openminion.modules.tool import ToolRegistry, ToolSpec


def _adapter(workspace_root: Path) -> ToolAdapter:
    registry = ToolRegistry()
    for name in ("safe.read", "danger.write"):
        registry.add(
            ToolSpec(
                name=name,
                args_model=dict,
                min_scope="READ_ONLY",
                handler=lambda _arguments, _ctx, tool_name=name: {
                    "ok": True,
                    "content": tool_name,
                },
            )
        )
    return ToolAdapter(workspace_root=workspace_root, runtime_registry=registry)


def test_turn_tool_allowlist_filters_catalog_and_execution(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    runner = SimpleNamespace(tool_api=adapter)

    with adapter.restrict_tools(("safe.read",)):
        assert collect_runtime_tool_names(runner) == frozenset({"safe.read"})
        assert RunnerToolCatalog(runner).list_tool_names() == {"safe.read"}
        assert RunnerToolCatalog(runner).get_tool_schema("danger.write") is None
        result = adapter.execute(
            command={"tool_name": "danger.write", "args": {}},
            session_id="child-session",
            trace_id="child-turn",
        )

    assert result["status"] == "error"
    assert result["error"]["code"] == "POLICY_DENIED"
    assert RunnerToolCatalog(runner).list_tool_names() == {
        "safe.read",
        "danger.write",
    }


def test_turn_tool_allowlist_uses_model_name_before_runtime_binding(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    registry.add(
        ToolSpec(
            name="fetch.get",
            args_model=dict,
            min_scope="READ_ONLY",
            handler=lambda _arguments, _ctx: {"ok": True, "content": "fetched"},
        )
    )
    adapter = ToolAdapter(workspace_root=tmp_path, runtime_registry=registry)

    with adapter.restrict_tools(("web.fetch",)):
        result = adapter.execute(
            command={"tool_name": "web.fetch", "args": {}},
            session_id="project-session",
            trace_id="project-turn",
        )

    assert result["status"] == "success"
    assert result["summary"] == "fetched"


def test_turn_tool_allowlist_is_isolated_between_concurrent_children(
    tmp_path: Path,
) -> None:
    adapter = _adapter(tmp_path)
    barrier = Barrier(2)

    def visible_tools(tool_name: str) -> frozenset[str]:
        with adapter.restrict_tools((tool_name,)):
            barrier.wait()
            runner = SimpleNamespace(tool_api=adapter)
            return collect_runtime_tool_names(runner)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = {
            future.result()
            for future in (
                executor.submit(visible_tools, "safe.read"),
                executor.submit(visible_tools, "danger.write"),
            )
        }

    assert results == {
        frozenset({"safe.read"}),
        frozenset({"danger.write"}),
    }


@pytest.mark.parametrize(
    "requested, expected", [("", ()), ("safe.read,danger.write", ("safe.read",))]
)
def test_sdk_allowlist_cannot_expand_delegated_scope(requested, expected):
    assert (
        turn_tool_allowlist(
            {
                "subagent_context_id": "child",
                "subagent_tool_allowlist": "safe.read",
                "turn_tool_allowlist_supplied": "true",
                "turn_tool_allowlist": requested,
            }
        )
        == expected
    )


@pytest.fixture
def repair_runtime(tmp_path):
    import subprocess
    import sys

    from openminion.modules.task import (
        TaskManager,
        build_autonomy_run,
        build_project_run_projection,
        save_project_run_checkpoint,
    )
    from openminion.modules.task.project.checkpoints import (
        initial_repository_lifecycle_payload,
    )
    from openminion.modules.task.project.models import SecurityRepairContext
    from openminion.modules.task.project.verification import (
        run_project_verification_commands,
        security_repair_file_identity,
    )
    from tests.artifact.utils import artifact_ctl

    repository = tmp_path / "candidate"
    repository.mkdir()
    (repository / "app.py").write_text("VALUE = 0\n")
    (repository / "verify.py").write_text("from app import VALUE\nassert VALUE == 1\n")
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "seed",
        ],
        cwd=repository,
        check=True,
    )
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repository, text=True
    ).strip()
    manager = TaskManager.for_lifecycle_db(db_path=tmp_path / "tasks.db")
    run = build_autonomy_run(
        goal_text="Repair selected finding",
        goal_id="goal",
        session_id="parent-session",
        workspace_ref=f"local:{repository}",
        max_iterations=2,
        verification_commands=(f"{sys.executable} -B verify.py",),
    ).model_copy(update={"task_id": "repair-task"})
    manager.create_task(
        session_id=run.session_id,
        mode_name="project",
        goal=run.goal_text,
        agent_id="parent",
        task_id=run.task_id,
    )
    project = build_project_run_projection(
        run,
        objective_ledger_ref="objective",
        evidence_ledger_ref="evidence",
        resume_packet_ref="resume",
        operator_decision_log_ref="decisions",
        capability_plan_ref="capabilities",
        metrics_summary_ref="metrics",
    )
    repair = SecurityRepairContext(
        source_task_id="scan-task",
        source_job_id="scan-job",
        source_run_id="scan-run",
        report_ref="artifact://report",
        review_ref="artifact://review",
        finding_id="finding",
        source_root=str(tmp_path / "monitored"),
        base_revision=revision,
        config_fingerprint="scan-config",
        repository=str(repository),
        write_paths=("app.py",),
        protected_files={
            "verify.py": security_repair_file_identity(repository / "verify.py")
        },
        evidence_refs=(),
        remediation_objective="VALUE must be 1",
        verification_criteria=("VALUE == 1",),
    )
    baseline = run_project_verification_commands(
        run.execution_selectors.verification_commands, workspace=repository
    )
    payload = initial_repository_lifecycle_payload(
        run,
        project,
        launch_approved=True,
        verification_commands=run.execution_selectors.verification_commands,
    )
    payload.update(
        security_repair=repair.model_dump(mode="json"),
        security_repair_baseline=[item.model_dump(mode="json") for item in baseline],
    )
    checkpoint = save_project_run_checkpoint(
        manager, project, checkpoint_id="repair-checkpoint", payload=payload
    )
    with artifact_ctl(tmp_path) as artifacts:
        adapter = ToolAdapter(
            workspace_root=repository,
            task_manager=manager,
            artifactctl=artifacts,
            agent_id="parent",
        )
        yield SimpleNamespace(
            adapter=adapter,
            manager=manager,
            repository=repository,
            checkpoint=checkpoint,
            artifacts=artifacts,
            context=repair,
        )
    manager.close()


@pytest.mark.parametrize("replay", [False, True])
@pytest.mark.parametrize(
    "tool,args",
    [
        ("file.write", {"path": "verify.py", "content": "pass\n"}),
        ("file.write", {"path": "app.py", "content": "VALUE = 1\n"}),
        (
            "code.patch",
            {"path": "app.py", "patch": "@@ -1 +1 @@\n-VALUE = 0\n+VALUE = 1\n"},
        ),
        ("code.patch", {"path": "verify.py", "patch": "@@ -1 +1 @@\n-x\n+y\n"}),
        ("exec.run", {"command": "touch forbidden"}),
        ("git.commit", {"message": "unapproved"}),
        ("task.delegate", {"mode": "list", "agent_id": "child", "limit": 20}),
        (
            "task.delegate",
            {"mode": "async", "agent_id": "child", "instruction": "escape"},
        ),
        (
            "task.delegate",
            {"mode": "sync", "agent_id": "child", "instruction": "escape"},
        ),
    ],
)
def test_security_repair_dispatch_restricts_bypass_and_replay(
    repair_runtime, replay, tool, args
):
    fixture = repair_runtime
    original = (fixture.repository / "verify.py").read_bytes()
    inputs = {"permission_mode": "bypass"}
    if replay:
        inputs.update(
            confirmation_grant_id="approved-call", confirmation_source="policy_replay"
        )
    result = fixture.adapter.execute(
        command={
            "tool_name": tool,
            "args": args,
            "inputs": inputs,
            "meta": {"orchestration": {"project_task_id": "repair-task"}},
        },
        session_id="parent-session",
        trace_id="repair-turn",
    )
    assert result["error"]["code"] == "POLICY_DENIED", result
    assert (fixture.repository / "verify.py").read_bytes() == original
    assert not (fixture.repository / "forbidden").exists()


def test_security_repair_allowed_tools_are_registered(repair_runtime):
    from openminion.modules.brain.adapters.tool.policy_context import (
        _SECURITY_REPAIR_TOOLS,
    )

    for name in _SECURITY_REPAIR_TOOLS:
        assert repair_runtime.adapter.registry.get(name).name == name


@pytest.mark.parametrize(
    "tool,args", [("file.read", {"path": "app.py"}), ("git.status", {})]
)
def test_security_repair_policy_is_local_to_run_and_preserves_original_denials(
    repair_runtime,
    tool,
    args,
):
    fixture = repair_runtime
    adapter = fixture.adapter
    original_paths = list(adapter.policy.raw["paths"]["write_allow"])
    command = {
        "tool_name": tool,
        "args": args,
        "inputs": {"permission_mode": "bypass"},
        "meta": {"orchestration": {"project_task_id": "repair-task"}},
    }
    result = adapter.execute(
        command=command, session_id="parent-session", trace_id="repair-turn"
    )
    assert result["status"] == "success", result
    assert adapter.policy.raw["paths"]["write_allow"] == original_paths
    assert "security_repair" not in adapter.policy.raw["context_metadata"]
    adapter.policy.raw["paths"]["deny"].append(str(fixture.repository / "app.py"))
    result = adapter.execute(
        command=command, session_id="parent-session", trace_id="denied-turn"
    )
    assert result["error"]["code"] == "POLICY_DENIED"


def test_security_repair_child_transport_enforces_canonical_lease(
    repair_runtime, tmp_path
):
    import json

    from openminion.modules.brain.adapters.a2a.runtime import A2actlAdapter
    from openminion.modules.brain.schemas import BudgetCounters, WorkingState
    from openminion.modules.brain.tools.executor.dispatch import (
        _inject_runtime_tool_metadata,
    )
    from openminion.modules.tool.runtime.delegation import A2aRuntimeDelegateAdapter
    from openminion.services.brain.post_execution.context import (
        _attach_delegation_resume_context,
    )

    fixture = repair_runtime
    results = []
    received_binding = []
    candidate_record = {}

    class Runtime:
        def list_registered_agents(self):
            return ["child", "reviewer"]

        def run_turn(self, *, payload, **_kwargs):
            workspace = Path(payload["inbound_metadata"]["workspace_root"])
            child = ToolAdapter(
                workspace_root=workspace,
                task_manager=fixture.manager,
                artifactctl=fixture.artifacts,
                agent_id=payload["agent_id"],
            )
            inline = {}
            _attach_delegation_resume_context(
                state_inline=inline, inbound_metadata=payload["inbound_metadata"]
            )
            state = WorkingState(
                session_id=payload["session_id"],
                agent_id=payload["agent_id"],
                budgets_remaining=BudgetCounters(
                    ticks=5, tool_calls=5, a2a_calls=1, tokens=1000, time_ms=10000
                ),
            )
            state.module_state = inline["module_state"]
            received_binding.append(state.module_state["security_repair_binding"])
            if payload["agent_id"] == "reviewer":
                child.policy.raw["paths"]["write_allow"] = []
                command = {
                    "tool_name": "file.read",
                    "args": {"path": "verify.py"},
                    "inputs": {"permission_mode": "bypass"},
                }
                _inject_runtime_tool_metadata(command, state=state, lineage={})
                read = child.execute(
                    command=command,
                    session_id=payload["session_id"],
                    trace_id="review-read",
                )
                assert read["status"] == "success", read
                command.update(
                    tool_name="file.write",
                    args={"path": "app.py", "content": "VALUE = 99\n"},
                )
                denied = child.execute(
                    command=command,
                    session_id=payload["session_id"],
                    trace_id="review-write",
                )
                assert denied["error"]["code"] == "POLICY_DENIED", denied
                return {
                    "body": "review passed",
                    "metadata": {
                        "brain_status": "done",
                        "delegation_result_summary": {
                            "summary": "review passed",
                            "status": "complete",
                            "review": {
                                "target_digest": candidate_record["target_digest"],
                                "verifier_refs": candidate_record["validation"][
                                    "verifier_refs"
                                ],
                                "passed": True,
                                "findings": [],
                            },
                        },
                    },
                }
            for tool, args in [
                ("exec.run", {"command": "touch forbidden"}),
                (
                    "task.delegate",
                    {
                        "mode": "sync",
                        "code_bearing": True,
                        "agent_id": "child",
                        "instruction": "escape",
                    },
                ),
                ("file.write", {"path": "verify.py", "content": "pass\n"}),
                ("file.write", {"path": "app.py", "content": "VALUE = 1\n"}),
                (
                    "code.patch",
                    {
                        "path": "app.py",
                        "patch": "@@ -1 +1 @@\n-VALUE = 0\n+VALUE = 1\n",
                    },
                ),
                ("file.write", {"path": "app.py", "content": "VALUE = 1\n"}),
            ]:
                command = {
                    "tool_name": tool,
                    "args": args,
                    "inputs": {"permission_mode": "bypass"},
                }
                _inject_runtime_tool_metadata(command, state=state, lineage={})
                results.append(
                    child.execute(
                        command=command,
                        session_id=payload["session_id"],
                        trace_id="child-turn",
                    )
                )
            assert all(
                item["error"]["code"] == "POLICY_DENIED" for item in results[:3]
            ), results
            assert results[3]["status"] == "success", results[3]
            forged = json.loads(received_binding[0])
            forged["binding_id"] = "not-the-active-lease"
            result = child.execute(
                command={
                    "tool_name": "file.write",
                    "args": {"path": "app.py", "content": "VALUE = 2\n"},
                    "inputs": {"permission_mode": "bypass"},
                    "meta": {
                        "orchestration": {"security_repair_binding": json.dumps(forged)}
                    },
                },
                session_id=payload["session_id"],
                trace_id="forged-turn",
            )
            assert result["error"]["code"] == "POLICY_DENIED", result
            return {"body": "implemented", "metadata": {"brain_status": "done"}}

    a2a = A2actlAdapter(
        home_root=tmp_path, agent_id="parent", runtime_resolver=lambda: Runtime()
    )
    fixture.adapter.a2a_delegate_api = A2aRuntimeDelegateAdapter(
        a2a_call=a2a.call, parent_agent_id="parent"
    )
    fixture.adapter.agent_query = lambda: [
        {"agent_id": "child"},
        {"agent_id": "reviewer"},
    ]
    try:
        result = fixture.adapter.execute(
            command={
                "tool_name": "task.delegate",
                "args": {
                    "mode": "sync",
                    "code_bearing": True,
                    "agent_id": "child",
                    "instruction": "Set VALUE to 1",
                    "timeout_seconds": 30,
                },
                "inputs": {"permission_mode": "bypass"},
                "meta": {"orchestration": {"project_task_id": "repair-task"}},
            },
            session_id="parent-session",
            trace_id="parent-turn",
        )
        assert result["status"] == "success", result
        assert (fixture.repository / "app.py").read_text() == "VALUE = 0\n"
        from openminion.modules.brain.execution.worktree_children import (
            load_child_worktree_record,
        )

        artifact = result["outputs"]["child_artifact"]
        candidate_record.update(
            load_child_worktree_record(fixture.artifacts, artifact["record_alias"])
        )
        reviewed = fixture.adapter.execute(
            command={
                "tool_name": "task.delegate",
                "args": {
                    "mode": "review",
                    "agent_id": "reviewer",
                    "instruction": "Review selected repair",
                    "child_artifact": artifact,
                    "review_criteria": ["VALUE == 1"],
                },
                "inputs": {"permission_mode": "bypass"},
                "meta": {"orchestration": {"project_task_id": "repair-task"}},
            },
            session_id="parent-session",
            trace_id="review-turn",
        )
        assert reviewed["status"] == "success", reviewed
        assert reviewed["outputs"]["status"] == "passed", reviewed
        assert received_binding
        assert (
            "security_repair_child"
            not in fixture.manager.get_task("repair-task").metadata
        )
    finally:
        a2a.close()


@pytest.mark.parametrize(
    "task_id,agent_id,restricted",
    [
        ("repair-task", "parent", True),
        ("repair-task", "foreign", False),
        ("missing-task", "parent", False),
        ("generic-task", "parent", False),
        (None, "parent", False),
    ],
)
def test_security_repair_delegate_schema_uses_canonical_owner(
    repair_runtime, task_id, agent_id, restricted
):
    from openminion.modules.brain.loop.tools.runtime import build_runtime_tool_specs

    if task_id == "generic-task":
        repair_runtime.manager.create_task(
            session_id="parent-session",
            mode_name="project",
            goal="Generic project",
            agent_id="parent",
            task_id=task_id,
            metadata={"security_repair": {"operator_approved": True}},
        )
    adapter = repair_runtime.adapter
    adapter.agent_id = agent_id
    runner = SimpleNamespace(tool_api=adapter)
    metadata = {
        "project_task_id": task_id,
        "security_repair": {"operator_approved": True},
    }
    specs = build_runtime_tool_specs(
        runner, allowed_tools=frozenset({"task.delegate"}), metadata=metadata
    )
    mode = specs[0].input_schema["properties"]["mode"]
    if restricted:
        assert mode["enum"] == ["sync", "review", "accept", "reject"]
        assert "list" not in mode["description"]
    else:
        assert "enum" not in mode
        assert "list" in mode["description"]
    generic = build_runtime_tool_specs(
        runner, allowed_tools=frozenset({"task.delegate"})
    )
    assert "enum" not in generic[0].input_schema["properties"]["mode"]


@pytest.mark.parametrize("replay", [False, True])
@pytest.mark.parametrize("mode", ["list", "async", "status", "result", "cancel"])
def test_security_repair_delegate_denied_before_confirmation(
    repair_runtime, mode, replay
):
    from unittest.mock import Mock
    from openminion.modules.brain.runner.delegates import _approve_delegate
    from openminion.modules.brain.schemas import (
        BudgetCounters,
        PolicyDecision,
        ToolCommand,
        WorkingState,
    )

    runner = SimpleNamespace(
        task_manager=repair_runtime.manager,
        policy_api=Mock(),
        _approval_callback=Mock(side_effect=AssertionError("unsupported approval")),
    )
    runner.policy_api.evaluate.return_value = PolicyDecision(
        outcome="REQUIRE_CONFIRMATION", approval_id="unreachable"
    )
    state = WorkingState(
        budgets_remaining=BudgetCounters(
            ticks=5, tool_calls=5, a2a_calls=2, tokens=5000, time_ms=60000
        ),
        session_id="parent-session",
        agent_id="parent",
        resume_task_id_hint="repair-task",
    )
    command = ToolCommand(
        title="delegate", tool_name="task.delegate", args={"mode": mode}
    )
    if replay:
        command.inputs.update(
            confirmation_source="policy_replay",
            confirmation_grant_id="local-confirmation-test",
        )
        state.local_confirmation_command_ids.append(command.command_id)
    result = _approve_delegate(runner, state=state, command=command, logger=Mock())
    assert result.title == "Policy denied"
    assert state.pending_policy_approval_id is None
    assert state.pending_confirmation_command is None
    assert state.local_confirmation_command_ids == []
    runner.policy_api.evaluate.assert_not_called()
    runner._approval_callback.assert_not_called()


@pytest.mark.parametrize(
    "task_id,agent_id,mode",
    [("repair-task", "parent", mode) for mode in ("sync", "review", "accept", "reject")]
    + [(None, "parent", "list"), ("repair-task", "foreign", "list")],
)
def test_security_repair_supported_and_generic_delegation_keep_confirmation(
    repair_runtime, task_id, agent_id, mode
):
    from unittest.mock import Mock
    from openminion.modules.brain.runner.delegates import _approve_delegate
    from openminion.modules.brain.schemas import (
        BudgetCounters,
        PolicyDecision,
        ToolCommand,
        WorkingState,
    )

    runner = SimpleNamespace(task_manager=repair_runtime.manager, policy_api=Mock())
    runner.policy_api.evaluate.return_value = PolicyDecision(
        outcome="REQUIRE_CONFIRMATION", approval_id="approval"
    )
    state = WorkingState(
        budgets_remaining=BudgetCounters(
            ticks=5, tool_calls=5, a2a_calls=2, tokens=5000, time_ms=60000
        ),
        session_id="parent-session",
        agent_id=agent_id,
        resume_task_id_hint=task_id,
    )
    result = _approve_delegate(
        runner,
        state=state,
        command=ToolCommand(
            title="delegate", tool_name="task.delegate", args={"mode": mode}
        ),
        logger=Mock(),
    )
    assert result.title == "Policy confirmation required"
    assert state.pending_policy_approval_id == "approval"
    runner.policy_api.evaluate.assert_called_once()


def test_security_repair_entry_native_schema(repair_runtime, tmp_path):
    from tests.brain.test_unified_turn_entry import (
        _build_runner,
        _RecordingEntryLLM,
        _text_response,
        _state,
    )
    from tests.brain.runner_test_support import fake_logger

    llm = _RecordingEntryLLM(_text_response("Ready"))
    runner = _build_runner(tmp_path, llm_api=llm)
    runner.tool_api = repair_runtime.adapter
    state = _state("parent-session")
    state.agent_id = "parent"
    state.resume_task_id_hint = "repair-task"
    runner._decide(
        state=state,
        user_input="Review the repair",
        logger=fake_logger(),
        forced_tools=["task.delegate"],
    )
    spec = next(tool for tool in llm.requests[0].tools if tool.name == "task.delegate")
    assert spec.input_schema["properties"]["mode"]["enum"] == [
        "sync",
        "review",
        "accept",
        "reject",
    ]


@pytest.mark.parametrize("replay", [False, True])
def test_security_repair_adaptive_native_schema(repair_runtime, monkeypatch, replay):
    from tests.brain.modes.test_act_adaptive import (
        _ctx,
        _FakeLLMClient,
        _FakeCommandExecutor,
    )
    from openminion.modules.brain.loop.adaptive import ActLoopMode
    from openminion.modules.brain.loop.tools import (
        AdaptiveToolLoopOutcome,
        AdaptiveToolLoopState,
    )
    from openminion.modules.brain.schemas import ToolCommand

    ctx, services = _ctx(_FakeLLMClient(), _FakeCommandExecutor())
    services.runner = SimpleNamespace(
        tool_api=repair_runtime.adapter,
        options=SimpleNamespace(failure_strategy="halt"),
    )
    ctx.state.agent_id = "parent"
    ctx.state.resume_task_id_hint = "repair-task"
    if replay:
        ctx.decision.reason_code = "confirmation_replay_validation"
        ctx.decision._seeded_commands = [
            ToolCommand(
                title="review", tool_name="task.delegate", args={"mode": "review"}
            )
        ]
    captured = []

    def run_loop(*args, **kwargs):
        captured.extend(kwargs["tool_specs"])
        profile = kwargs["profile"]
        return AdaptiveToolLoopOutcome(
            profile_name=profile.profile_name,
            mode_name=profile.mode_name,
            termination_reason="final_text",
            state=AdaptiveToolLoopState(),
            allowed_tools=profile.allowed_tools,
            mode_result=SimpleNamespace(
                status="done", working_state=ctx.state, message="ok"
            ),
        )

    monkeypatch.setattr(
        "openminion.modules.brain.loop.adaptive.run_adaptive_tool_loop", run_loop
    )
    with repair_runtime.adapter.restrict_tools(("task.delegate",)):
        ActLoopMode().execute(ctx)
    spec = next(tool for tool in captured if tool.name == "task.delegate")
    assert spec.input_schema["properties"]["mode"]["enum"] == [
        "sync",
        "review",
        "accept",
        "reject",
    ]


@pytest.mark.parametrize("adapter_kind", ["adaptive", "coding"])
@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize(
    "case", ["security_deny", "generic_deny", "stale_deny", "confirm"]
)
def test_policy_denial_is_not_project_confirmation(
    repair_runtime, tmp_path, adapter_kind, prepared, case
):
    from unittest.mock import Mock
    from tests.brain.test_unified_turn_entry import _build_runner, _state
    from tests.brain.modes.test_act_adaptive import _ctx, _FakeLLMClient
    from openminion.modules.brain.loop.adaptive.context import (
        _AdaptiveLoopContextAdapter,
    )
    from openminion.modules.brain.loop.strategies.coding.context_adapter import (
        _CodingLoopContextAdapter,
    )
    from openminion.modules.brain.tools.executor import RunnerCommandExecutor
    from openminion.modules.brain.schemas import PolicyDecision, ToolCommand
    from openminion.modules.task import save_project_run_checkpoint
    from openminion.modules.task.project.models import ProjectCycleDecision
    from openminion.modules.task.project.turn import (
        project_turn_result_from_response,
        project_cycle_checkpoint_payload,
    )
    from openminion.services.brain.post_execution.postprocess_response import (
        _attach_postprocess_action_metadata,
    )

    runner = _build_runner(tmp_path, llm_api=SimpleNamespace())
    runner.tool_api = repair_runtime.adapter
    runner.task_manager = repair_runtime.manager
    runner.policy_api = Mock()
    runner.policy_api.evaluate.return_value = PolicyDecision(
        outcome="REQUIRE_CONFIRMATION" if case == "confirm" else "DENY",
        explanation="Operator review required"
        if case == "confirm"
        else "Denied by policy",
        approval_id="approval" if case == "confirm" else None,
    )
    state = _state("parent-session")
    state.agent_id = "parent"
    state.resume_task_id_hint = None if case == "generic_deny" else "repair-task"
    command = ToolCommand(
        title="Delegate",
        tool_name="task.delegate",
        args={"mode": "review" if case == "confirm" else "list"},
    )
    if case == "stale_deny":
        state.pending_confirmation_command = command.model_copy(deep=True)
        state.pending_policy_approval_id = "stale-approval"
        state.pending_confirmation_goal = "stale goal"
    executor = RunnerCommandExecutor(runner)
    ctx, services = _ctx(_FakeLLMClient(), executor, state=state)
    services.runner = runner
    adapter_type = (
        _AdaptiveLoopContextAdapter
        if adapter_kind == "adaptive"
        else _CodingLoopContextAdapter
    )
    adapter = adapter_type(ctx)
    if prepared:
        dispatch = adapter.prepare_tool_dispatch(command=command)
        outcome = adapter.finalize_prepare_outcome(prepare_outcome=dispatch)
    else:
        outcome = adapter.execute_command(command=command)
    assert outcome.action_result.status == "needs_user"
    assert state.post_action_user_message
    metadata = {"brain_status": "waiting_user"}
    _attach_postprocess_action_metadata(
        SimpleNamespace(_attach_tool_result_metadata=Mock()),
        runner=runner,
        metadata=metadata,
        step_out=SimpleNamespace(
            working_state=state, action_result=outcome.action_result
        ),
        tool_results_payload=[],
        termination_reason="needs_user",
        finalization_payload=None,
    )
    turn = project_turn_result_from_response(
        response={"metadata": metadata, "summary": state.post_action_user_message}
    )
    payload = project_cycle_checkpoint_payload(
        repair_runtime.checkpoint,
        turn,
        effect_payload={},
        plan_payload={},
        repository_payload={},
        decision=ProjectCycleDecision.NEEDS_INPUT,
        verification=(),
        verification_closure={},
        decision_reason="needs_user",
        replan_count=0,
        waiting_for_checks=False,
        operator_guidance_consumed_revision=0,
    )
    saved = save_project_run_checkpoint(
        repair_runtime.manager,
        repair_runtime.checkpoint.project_run,
        checkpoint_id="post-policy",
        payload=payload,
    )
    checkpoint = repair_runtime.manager.get_checkpoint(
        saved.project_run.task_id, saved.checkpoint_id
    )
    expected = command.command_id if case == "confirm" else ""
    assert checkpoint["payload"]["pending_confirmation_command_id"] == expected
    if case == "confirm":
        assert state.pending_confirmation_command.command_id == command.command_id
        assert state.pending_policy_approval_id == "approval"
    else:
        assert outcome.approved_command.disposition == "policy_denied"
        assert state.pending_confirmation_command is None
        assert state.pending_policy_approval_id is None
        assert state.pending_confirmation_goal is None
        assert state.post_action_user_message == outcome.approved_command.question
        assert "pending_confirmation_command_id" not in metadata


@pytest.mark.parametrize("task_id", ["repair-task", None])
def test_coding_schema_preserves_security_scope(repair_runtime, task_id):
    from tests.brain.modes.test_act_adaptive import (
        _ctx,
        _FakeLLMClient,
        _FakeCommandExecutor,
    )
    from openminion.modules.brain.loop.strategies.coding.runtime import (
        _build_tool_specs,
    )

    ctx, services = _ctx(_FakeLLMClient(), _FakeCommandExecutor())
    services.runner = SimpleNamespace(tool_api=repair_runtime.adapter)
    ctx.state.resume_task_id_hint = task_id
    specs = _build_tool_specs(frozenset({"task.delegate"}), ctx=ctx)
    mode = specs[0].input_schema["properties"]["mode"]
    assert mode.get("enum") == (
        ["sync", "review", "accept", "reject"] if task_id else None
    )


@pytest.mark.parametrize("task_id", ["repair-task", None])
def test_budget_shortlisting_preserves_security_scope(
    repair_runtime, tmp_path, monkeypatch, task_id
):
    from unittest.mock import Mock
    from tests.brain.test_unified_turn_entry import _build_runner, _state
    from openminion.modules.brain.bootstrap.budget import trim_decide_context_to_budget
    from openminion.modules.brain.tools.schema import (
        collect_runtime_tool_schemas,
        runtime_tool_scope,
    )

    runner = _build_runner(tmp_path, llm_api=SimpleNamespace())
    runner.tool_api = repair_runtime.adapter
    state = _state("parent-session")
    state.resume_task_id_hint = task_id
    state.budgets_remaining.tokens = 100
    rebuilt = Mock(side_effect=[({}, 200), ({}, 1)])
    monkeypatch.setattr(
        "openminion.modules.brain.bootstrap.budget._rebuild_decide_context_with_hints",
        rebuilt,
    )
    with repair_runtime.adapter.restrict_tools(("task.delegate",)):
        hints = {
            "runtime_tool_schemas": collect_runtime_tool_schemas(
                runner, metadata=runtime_tool_scope(state)
            )
        }
        _, estimate, decision = trim_decide_context_to_budget(
            runner=runner,
            state=state,
            logger=Mock(),
            model="test",
            budget_max_tokens=100,
            hints=hints,
            context={},
            estimate=300,
            user_input="task.delegate",
        )
    assert estimate == 1 and decision is None
    assert rebuilt.call_count == 2
    mode = hints["runtime_tool_schemas"][0]["parameters"]["properties"]["mode"]
    assert mode.get("enum") == (
        ["sync", "review", "accept", "reject"] if task_id else None
    )


@pytest.mark.parametrize("task_id", ["repair-task", None])
def test_feasibility_prompt_preserves_security_scope(
    repair_runtime, tmp_path, monkeypatch, task_id
):
    from unittest.mock import Mock
    from tests.brain.test_unified_turn_entry import _build_runner, _state
    from openminion.modules.brain.schemas import Plan, SubIntent, ToolCommand
    from openminion.modules.brain.execution.runtime.turn.feasibility import (
        assess_plan_feasibility,
    )

    runner = _build_runner(tmp_path, llm_api=SimpleNamespace())
    runner.tool_api = repair_runtime.adapter
    runner._build_context = Mock(
        side_effect=lambda **kwargs: {"hints": kwargs["hints"]}
    )
    state = _state("parent-session")
    state.resume_task_id_hint = task_id
    state.plan = Plan(
        objective="Review and accept repair",
        stop_conditions=["done"],
        assumptions=[],
        risk_summary="low",
        success_criteria={},
        sub_intents=[SubIntent(id="repair", description="Review and accept")],
        steps=[
            ToolCommand(
                title=mode,
                tool_name="task.delegate",
                args={"mode": mode},
                sub_intent_ids=["repair"],
            )
            for mode in ("review", "accept")
        ],
    )
    structured = Mock(
        return_value={
            "plan_viable": True,
            "assessments": [
                {
                    "intent_id": "repair",
                    "status": "covered",
                    "covering_tools": ["task.delegate"],
                }
            ],
        }
    )
    monkeypatch.setattr(
        "openminion.modules.brain.execution.runtime.turn.feasibility.call_structured_with_retry",
        structured,
    )
    report = assess_plan_feasibility(
        runner, state=state, user_input="Review repair", logger=Mock()
    )
    assert report.plan_viable
    schemas = structured.call_args.kwargs["context"]["hints"]["runtime_tool_schemas"]
    mode = next(item for item in schemas if item["name"] == "task.delegate")[
        "parameters"
    ]["properties"]["mode"]
    assert mode.get("enum") == (
        ["sync", "review", "accept", "reject"] if task_id else None
    )
