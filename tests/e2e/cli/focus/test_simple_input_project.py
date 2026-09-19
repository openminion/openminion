"""Provider-free Focus handoff, durable criteria, controls, and restart journey."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import shlex
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from openminion.api.queries.tasks import show_task
from openminion.base.types import Message
from openminion.modules.brain.adapters.session import SessctlAdapter
from openminion.modules.brain.adapters.tool import ToolAdapter
from openminion.modules.brain.execution.entry import (
    build_execution_entry_request,
    dispatch,
)
from openminion.modules.brain.paths import resolve_brain_sessions_db_path
from openminion.modules.brain.schemas import ActDecision
from openminion.modules.task import (
    AutonomyRunStore,
    TaskManager,
    load_latest_project_checkpoint,
    TaskLifecycleState,
)
from openminion.modules.tool.registry import ToolRegistry
from openminion.modules.task.autonomy import (
    AutonomyRunStatus,
    resolve_autonomy_state_root,
)
from openminion.modules.task.constants import DEFAULT_INTEGRATED_SQLITE_SUBPATH
from openminion.modules.task.plan import (
    TaskPlan,
    TaskPlanRevision,
    TaskPlanStepCompleted,
    TaskPlanTerminalSignal,
)
from openminion.modules.task.project import (
    ProjectCycleDecision,
    run_project_verification_commands,
)
from openminion.services.brain.post_execution.postprocess_metadata import (
    _attach_structured_action_output_metadata,
)
from openminion.services.runtime.a2a_delegate import A2aRuntimeDelegateAdapter
from openminion.services.runtime.project_worker import ProjectTurnResult, ProjectWorker
from openminion.tools.agent.plugin import _h_task_delegate
from tests.artifact.utils import artifact_ctl
from tests.brain.test_decision_readiness import (
    _FakeDirectDispatchHarness,
    _FakeRunner,
    _install_direct_dispatch_capture,
    _state,
)
from tests.e2e.cli.focus.test_goal_composition import _CronStore, _project_runtime
from tests.e2e.project_worker.test_repository_lifecycle import _child_artifact, _git
from tests.tools.search.test_provider_chain import _ContextAwareBraveProvider
from tests.tools.fetch.test_plugin import _FakeProvider

pytestmark = pytest.mark.e2e


def _proposal(runtime, monkeypatch, **changes) -> dict[str, str]:
    handoff = {
        "goal": "Research the fixture, fix feature.py, and prove VALUE equals 2",
        "success_criteria": ["feature.VALUE equals 2"],
        "verification_commands": [
            f"{shlex.quote(sys.executable)} -c 'from feature import VALUE; assert VALUE == 2'"
        ],
        "max_iterations": 3,
        **changes,
    }
    decision = ActDecision(
        act_profile="coding",
        sub_intents=["Inspect", "Research", "Implement", "Verify", "Review"],
        request_readiness={
            "posture": "review_before_act",
            "requested_outcome": "execute",
            "state": "needs_plan_review",
            "project_handoff": handoff,
        },
    )
    state = _state(session_id=runtime._turn_session_id())
    state.agent_id = runtime.agent_id
    harness = _FakeDirectDispatchHarness()
    _install_direct_dispatch_capture(monkeypatch, harness)
    result = dispatch(
        runner=_FakeRunner([decision]),
        state=state,
        logger=MagicMock(),
        request=build_execution_entry_request(
            user_input="Please fix this project",
            forced_tools=None,
            capability_category=None,
        ),
    )
    assert result.status == "waiting_user"
    assert harness.prepare_calls == 0 and harness.invoke_calls == []
    runtime._rt.storage_path = Path(runtime.working_dir) / "storage.db"
    session = SessctlAdapter(
        resolve_brain_sessions_db_path(storage_path=runtime._rt.storage_path)
    )
    try:
        session.put_working_state(
            runtime._turn_session_id(), state_inline=state.model_dump(mode="json")
        )
    finally:
        session.close()
    metadata = {}
    _attach_structured_action_output_metadata(
        metadata=metadata, action_result=result.action_result
    )
    return metadata


def _manager(runtime) -> TaskManager:
    return TaskManager.for_lifecycle_db(
        db_path=runtime._rt.data_root / DEFAULT_INTEGRATED_SQLITE_SUBPATH,
    )


def test_focus_plain_request_approval_verifier_repair_review_restart_and_controls(
    tmp_path, monkeypatch
) -> None:
    runtime, _, _ = _project_runtime(tmp_path)
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "silc@example.invalid")
    _git(tmp_path, "config", "user.name", "SILC Fixture")
    (tmp_path / "feature.py").write_text("VALUE = 0\n")
    _git(tmp_path, "add", "feature.py")
    _git(tmp_path, "commit", "-q", "-m", "seed fixture")
    metadata = _proposal(runtime, monkeypatch)
    cron = _CronStore()
    monkeypatch.setattr(
        "openminion.cli.commands.autonomy_project.configured_cron_store",
        lambda *a, **k: cron,
    )
    approvals = []

    async def approve(name, args, call_id):
        approvals.append((name, args, call_id))
        return True

    async def respond(**kwargs):
        return Message(
            channel="console", target="focus", body="Proposed plan", metadata=metadata
        )

    runtime._gateway = SimpleNamespace(handle_message=respond)
    monkeypatch.setattr(runtime, "_begin_turn_usage_tracking", lambda: None)
    monkeypatch.setattr(runtime, "_finalize_turn_usage", lambda *a, **k: None)
    monkeypatch.setattr(
        runtime, "_prepare_gateway_turn", lambda *a, **k: ({}, None, None)
    )

    async def send():
        return "".join(
            [
                chunk
                async for chunk in runtime._send_message_impl(
                    "Please fix this project", approval_callback=approve
                )
            ]
        )

    assert "Project queued:" in asyncio.run(send())
    store = AutonomyRunStore(root=resolve_autonomy_state_root(runtime._rt.home_root))
    run = store.list_runs()[0]
    manager = _manager(runtime)
    checkpoint = load_latest_project_checkpoint(manager, task_id=run.task_id)
    objective = checkpoint.payload["repository_lifecycle"][
        checkpoint.project_run.objective_ledger_ref
    ]
    assert (
        objective["success_criteria"]
        == approvals[0][1]["success_criteria"]
        == ["feature.VALUE equals 2"]
    )
    assert approvals[0][1]["permission_profile_id"] == runtime.permission_mode
    assert len(cron.jobs) == 1
    assert "nothing was launched" in asyncio.run(
        runtime.approve_project_handoff(metadata, approve)
    )
    assert len(store.list_runs()) == len(cron.jobs) == 1


    plan = TaskPlan(
        plan_id="silc-plan",
        objective=run.goal_text,
        criterion_ids=objective["criterion_ids"],
        status="completed",
        steps=[
            {"step_id": name, "description": name, "status": "completed"}
            for name in ("inspect", "research", "implement", "verify", "review")
        ],
    )
    from openminion.tools.search import plugin as search_plugin
    from openminion.tools.search.providers.registry import SearchProviderRegistry
    from openminion.tools.fetch import plugin as fetch_plugin
    from openminion.tools.fetch.providers import FetchProviderRegistry

    search_providers = SearchProviderRegistry()
    search_providers.register(_ContextAwareBraveProvider(healthy_without_ctx=True))
    monkeypatch.setattr(search_plugin, "provider_registry", lambda: search_providers)
    monkeypatch.setattr(search_providers, "load_entry_points", lambda: [])
    fetch_providers = FetchProviderRegistry()
    fetch_providers.register(_FakeProvider())
    monkeypatch.setattr(
        fetch_plugin, "_ensure_provider_registry", lambda: fetch_providers
    )
    registry = ToolRegistry()
    search_plugin.register(registry)
    fetch_plugin.register(registry)
    adapter = ToolAdapter(
        workspace_root=tmp_path,
        runtime_registry=registry,
        policy={
            "workspace_root": str(tmp_path),
            "tools": {"allow_exact": ["web.search", "web.fetch"]},
        },
        task_manager=manager,
        agent_id=runtime.agent_id,
    )

    def implement(request):
        assert (
            "web.search" in request.allowed_tools
            and "web.fetch" in request.allowed_tools
        )
        assert objective["criterion_ids"][0] in request.prompt
        searched = adapter.execute(
            command={
                "tool_name": "web.search",
                "args": {"query": "fixture documentation", "provider": "brave"},
            },
            session_id=run.session_id,
            trace_id="silc-search",
        )
        assert searched["status"] == "success", searched
        fetched = adapter.execute(
            command={
                "tool_name": "web.fetch",
                "args": {"url": "https://example.com/brave"},
            },
            session_id=run.session_id,
            trace_id="silc-fetch",
        )
        assert fetched["status"] == "success", fetched
        denied = adapter.execute(
            command={
                "tool_name": "file.write",
                "args": {"path": "forbidden.txt", "content": "no"},
            },
            session_id=run.session_id,
            trace_id="silc-denied-write",
        )
        assert denied["status"] != "success"
        assert not (tmp_path / "forbidden.txt").exists()
        (tmp_path / "feature.py").write_text("VALUE = 1\n")
        _git(tmp_path, "add", "feature.py")
        _git(
            tmp_path, "commit", "-q", "-m", "record implementation before verification"
        )
        return ProjectTurnResult(
            summary="Not complete: verifier will check the implementation",
            gateway_run_id="fixture:implementation-turn",
            task_plan=plan,
            evidence_refs=("fixture:search", "fixture:fetch", "fixture:implementation"),
            tool_call_count=3,
        )

    first = ProjectWorker(
        task_manager=manager,
        autonomy_store=store,
        turn=implement,
        verify=lambda: run_project_verification_commands(
            run.execution_selectors.verification_commands, workspace=tmp_path
        ),
    ).run_cycle(run.run_id)
    assert first.decision == ProjectCycleDecision.CONTINUE
    checkpoint = load_latest_project_checkpoint(manager, task_id=run.task_id)
    assert checkpoint.payload["plan_revision_required"] is True, (
        first.verification,
        checkpoint.payload["decision_reason"],
    )
    manager.close()
    paused = runtime.execute_project_control(f"/project pause {run.run_id}")[1]
    assert "task_state: paused" in paused
    cron.jobs.clear()  # The prior delivered wake is no longer pending.
    assert (
        "status: running"
        in runtime.execute_project_control(f"/project resume {run.run_id}")[1]
    )
    assert cron.jobs[0]["job_id"] == checkpoint.project_run.next_wake_job_id
    assert (
        "status: running"
        in runtime.execute_project_control(f"/project resume {run.run_id}")[1]
    )
    assert len(cron.jobs) == 1
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            "from tests.e2e.cli.focus.test_simple_input_project import _restart_and_repair; import sys; _restart_and_repair(sys.argv[1], sys.argv[2])",
            str(tmp_path),
            run.run_id,
        ],
        cwd=Path(__file__).resolve().parents[4],
        env={**os.environ, "PYTHONPATH": "src", "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert child.returncode == 0, child.stdout + child.stderr
    manager = _manager(runtime)
    final = load_latest_project_checkpoint(manager, task_id=run.task_id)
    assert final.project_run.committed_cycle_count == 2
    assert final.payload["task_plan"]["plan_id"] == plan.plan_id
    assert final.payload["plan_revision_count"] == 1
    assert (
        final.payload["repository_lifecycle"][final.project_run.objective_ledger_ref][
            "success_criteria"
        ]
        == objective["success_criteria"]
    )
    assert store.require(run.run_id).status == AutonomyRunStatus.COMPLETED
    assert _git(tmp_path, "diff", "HEAD", "--", "feature.py").endswith("+VALUE = 2")
    report = show_task(
        runtime=SimpleNamespace(task_manager=manager),
        task_id=run.task_id,
        agent_id=run.execution_selectors.agent_id,
        session_id=run.session_id,
    )
    assert report["project_report"]["project_run"]["task_id"] == run.task_id
    assert report["project_report"]["outcome"] == "completed-verified"
    assert report["project_report"]["task_plan"]["plan_id"] == plan.plan_id
    assert report["project_report"]["budget"]["used"]["tool_calls"] == 3
    assert "completed-verified" in runtime.execute_project_control("/project status")[1]
    with pytest.raises(ValueError, match="already completed"):
        runtime.execute_project_control(f"/project resume {run.run_id}")
    manager.close()


def _restart_and_repair(root: str, run_id: str) -> None:
    workspace = Path(root)
    store = AutonomyRunStore(root=resolve_autonomy_state_root(workspace / "home"))
    manager = TaskManager.for_lifecycle_db(
        db_path=workspace / "data" / DEFAULT_INTEGRATED_SQLITE_SUBPATH
    )
    run = store.require(run_id)
    checkpoint = load_latest_project_checkpoint(manager, task_id=run.task_id)
    objective = checkpoint.payload["repository_lifecycle"][
        checkpoint.project_run.objective_ledger_ref
    ]
    assert all(
        step["status"] == "completed"
        for step in checkpoint.payload["task_plan"]["steps"]
    )

    def repair(request):
        assert "Prior verifier outcome:" in request.prompt
        with artifact_ctl(workspace / "review-artifacts") as ctl:
            record = _child_artifact(
                workspace,
                ctl,
                subtask_id="silc-repair",
                value=2,
                verifier_ref="fixture:child-verifier",
                session_id=run.session_id,
            )

            def review_call(**kwargs):
                return {
                    "status": "success",
                    "summary": "Reviewed the repair",
                    "outputs": {
                        "child_agent_id": "reviewer",
                        "passed": True,
                        "findings": [],
                        "target_digest": record["target_digest"],
                        "verifier_refs": ["fixture:child-verifier"],
                    },
                }

            context = SimpleNamespace(
                a2a_delegate_api=A2aRuntimeDelegateAdapter(
                    a2a_call=review_call,
                    parent_agent_id=run.execution_selectors.agent_id,
                ),
                artifactctl=ctl,
                session_id=run.session_id,
                workspace=workspace,
                policy=SimpleNamespace(raw={}),
            )
            child_ref = {
                "record_alias": record["record_alias"],
                "target_digest": record["target_digest"],
            }
            from openminion.modules.tool.errors import ToolRuntimeError

            with pytest.raises(ToolRuntimeError):
                _h_task_delegate(
                    {"mode": "accept", "child_artifact": child_ref}, context
                )
            review = _h_task_delegate(
                {
                    "mode": "review",
                    "agent_id": "reviewer",
                    "instruction": "Review the repair",
                    "review_criteria": ["VALUE equals 2"],
                    "repository_instructions": "",
                    "child_artifact": child_ref,
                },
                context,
            )
            assert review["status"] == "passed"
            assert (workspace / "feature.py").read_text() == "VALUE = 1\n"
            assert (
                _h_task_delegate(
                    {"mode": "accept", "child_artifact": child_ref}, context
                )["status"]
                == "accepted"
            )
        return ProjectTurnResult(
            summary="Repaired and independently reviewed",
            gateway_run_id="fixture:repair-turn",
            task_plan_revision=TaskPlanRevision(
                plan_id="silc-plan",
                revision_id="silc-repair-revision",
                criterion_ids=objective["criterion_ids"],
                verifier_refs=checkpoint.project_run.verifier_refs,
                revised_steps=[
                    {"step_id": "repair", "description": "Repair the failed verifier"}
                ],
            ),
            task_plan_step_completed=TaskPlanStepCompleted(
                plan_id="silc-plan", step_id="repair", outcome="passed"
            ),
            task_plan_completed=TaskPlanTerminalSignal(
                plan_id="silc-plan", reason="Repair verified"
            ),
            evidence_refs=("fixture:repair", "fixture:independent-review"),
        )

    result = ProjectWorker(
        task_manager=manager,
        autonomy_store=store,
        turn=repair,
        verify=lambda: run_project_verification_commands(
            run.execution_selectors.verification_commands, workspace=workspace
        ),
    ).run_cycle(run_id)
    assert result.decision == ProjectCycleDecision.STOP
    manager.close()


def test_focus_project_handoff_consumes_conversation_scoped_state(
    tmp_path, monkeypatch
) -> None:
    runtime, _, _ = _project_runtime(tmp_path)
    runtime._conversation_id = f"focus-{runtime.session_id}"
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "silc@example.invalid")
    _git(tmp_path, "config", "user.name", "SILC Fixture")
    metadata = _proposal(runtime, monkeypatch)
    cron = _CronStore()
    monkeypatch.setattr(
        "openminion.cli.commands.autonomy_project.configured_cron_store",
        lambda *a, **k: cron,
    )

    async def approve(name, args, call_id):
        return True

    result = asyncio.run(runtime.approve_project_handoff(metadata, approve))

    assert "Project queued:" in result
    assert len(cron.jobs) == 1
    run = AutonomyRunStore(
        root=resolve_autonomy_state_root(runtime._rt.home_root)
    ).list_runs()[0]
    assert run.execution_selectors.turn_target == "focus"


@pytest.mark.parametrize("approved", [False, None])
def test_handoff_denial_or_unsupported_client_never_launches(
    tmp_path, monkeypatch, approved
) -> None:
    runtime, _, _ = _project_runtime(tmp_path)
    metadata = _proposal(runtime, monkeypatch)

    async def approve(*args):
        return approved

    result = asyncio.run(
        runtime.approve_project_handoff(metadata, None if approved is None else approve)
    )
    assert result == "" if approved is None else result == "Project launch denied."
    assert (
        AutonomyRunStore(
            root=resolve_autonomy_state_root(runtime._rt.home_root)
        ).list_runs()
        == []
    )
    assert not (tmp_path / "feature.py").exists()


def test_handoff_outside_workspace_fails_before_approval(tmp_path, monkeypatch) -> None:
    runtime, _, _ = _project_runtime(tmp_path)
    metadata = _proposal(runtime, monkeypatch, repository=str(tmp_path.parent))

    async def approve(*args):
        pytest.fail("Out-of-bound proposal requested approval")

    with pytest.raises(ValueError, match="inside the workspace"):
        asyncio.run(runtime.approve_project_handoff(metadata, approve))


def test_empty_verification_blocks_before_task_or_wake(tmp_path, monkeypatch) -> None:
    runtime, _, _ = _project_runtime(tmp_path)
    metadata = _proposal(runtime, monkeypatch, verification_commands=[])
    cron = _CronStore()
    monkeypatch.setattr(
        "openminion.cli.commands.autonomy_project.configured_cron_store",
        lambda *a, **k: cron,
    )

    async def approve(*args):
        return True

    assert "Project blocked:" in asyncio.run(
        runtime.approve_project_handoff(metadata, approve)
    )
    run = AutonomyRunStore(
        root=resolve_autonomy_state_root(runtime._rt.home_root)
    ).list_runs()[0]
    assert run.status == AutonomyRunStatus.BLOCKED
    manager = _manager(runtime)
    try:
        assert manager.get_task(run.task_id) is None and cron.jobs == []
    finally:
        manager.close()


@pytest.mark.parametrize("boundary", ["session", "agent"])
def test_project_controls_reject_foreign_owner(tmp_path, monkeypatch, boundary) -> None:
    runtime, _, _ = _project_runtime(tmp_path)
    cron = _CronStore()
    monkeypatch.setattr(
        "openminion.cli.commands.autonomy_project.configured_cron_store",
        lambda *a, **k: cron,
    )
    request = runtime.prepare_project_command(
        f"/project start --goal fixture --verify-command '{sys.executable} -c pass'"
    )
    assert runtime.launch_prepared_project(request)[0] == "system"
    if boundary == "session":
        runtime._session_id = "another-session"
    else:
        runtime._agent_id = "another-agent"
    with pytest.raises(ValueError, match="another session or agent"):
        runtime.execute_project_control(f"/project cancel {request.run.run_id}")
    assert len(cron.jobs) == 1


def test_project_controls_select_exact_run_cancel_and_report_current_state(
    tmp_path, monkeypatch
) -> None:
    runtime, _, _ = _project_runtime(tmp_path)
    cron = _CronStore()
    monkeypatch.setattr(
        "openminion.cli.commands.autonomy_project.configured_cron_store",
        lambda *a, **k: cron,
    )
    requests = [
        runtime.prepare_project_command(
            f"/project start --goal fixture{index} --verify-command '{sys.executable} -c pass'"
        )
        for index in range(2)
    ]
    for request in requests:
        assert runtime.launch_prepared_project(request)[0] == "system"
    with pytest.raises(ValueError, match="exact RUN_ID"):
        runtime.execute_project_control("/project status")
    with pytest.raises(KeyError):
        runtime.execute_project_control("/project show missing")
    with pytest.raises(ValueError, match="exact RUN_ID"):
        runtime.execute_project_control("/project cancel")
    cancelled = runtime.execute_project_control(
        f"/project cancel {requests[0].run.run_id}"
    )[1]
    assert "cancelled" in cancelled
    store = AutonomyRunStore(root=resolve_autonomy_state_root(runtime._rt.home_root))
    assert store.require(requests[0].run.run_id).status == AutonomyRunStatus.CANCELLED
    assert store.require(requests[1].run.run_id).status == AutonomyRunStatus.RUNNING
    assert len(cron.jobs) == 1
    manager = _manager(runtime)
    try:
        report = show_task(
            runtime=SimpleNamespace(task_manager=manager),
            task_id=store.require(requests[0].run.run_id).task_id,
            agent_id=runtime.agent_id,
            session_id=runtime.session_id,
        )
        assert report["project_report"]["outcome"] == "cancelled"
        assert (
            show_task(
                runtime=SimpleNamespace(task_manager=manager),
                task_id=store.require(requests[0].run.run_id).task_id,
                agent_id=runtime.agent_id,
                session_id="other",
            )
            is None
        )
        assert (
            manager.get_task(store.require(requests[1].run.run_id).task_id).state
            == TaskLifecycleState.ACTIVE
        )
    finally:
        manager.close()
