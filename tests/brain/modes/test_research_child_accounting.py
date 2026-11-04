from types import SimpleNamespace
from asyncio import CancelledError

from openminion.modules.brain.schemas import BudgetCounters
from openminion.modules.telemetry.usage.types import RunStats
from openminion.services.brain.post_execution.postprocess_metadata import (
    _attach_tool_result_metadata,
)
from openminion.services.brain.post_execution.postprocess_sources import (
    _cumulative_tool_results_from_step_output,
)

import pytest
from openminion.modules.brain.schemas import ActionResult
from openminion.modules.brain.tools.executor import CommandExecutionOutcome
from openminion.modules.brain.loop.tools import run_adaptive_tool_loop
from openminion.modules.llm.schemas import LLMResponse, Message, ToolCall, UsageInfo
from openminion.modules.task import TaskManager
from tests.brain.modes.test_research_contract import _ctx, _make_mode
from tests.brain.tool_loops.test_engine import (
    _FakeRuntime,
    _LoopContext,
    _profile,
    _tool_specs,
)


@pytest.mark.parametrize("parent_tool_budget", [9, 3])
@pytest.mark.parametrize("reuse_call_ids", [False, True])
@pytest.mark.parametrize("parent_collision", [False, True])
def test_enclosing_budget_caps_three_actual_adaptive_child_loops(
    tmp_path, monkeypatch, parent_tool_budget, reuse_call_ids, parent_collision
):
    tm = TaskManager.for_lifecycle_db(db_path=tmp_path / "tasks.db")
    ctx, _ = _ctx(tm, research_query="Gather evidence in up to three passes.")
    ctx.state.budgets_remaining.tool_calls = parent_tool_budget
    ctx.state.budgets_remaining.tokens = 6000
    ctx.state.llm_calls_max = 30
    mode = _make_mode(max_iterations=3)
    runs = []
    parent_records = []
    if parent_collision:
        parent_records = [
            {
                "call_id": "read-0" if reuse_call_ids else "pass-0-read-0",
                "tool_name": "file.read",
                "ok": True,
                "content": "parent evidence",
                "turn_scope_id": ctx.state.trace_id,
            }
        ]
        ctx.state.module_state["adaptive_loop"] = {"tool_results": parent_records}

    def invoke_child(runner, **kwargs):
        child = kwargs["state"]
        i = len(runs)
        responses = [
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                tool_calls=[
                    ToolCall(
                        id=f"read-{k}" if reuse_call_ids else f"pass-{i}-read-{k}",
                        name="file.read",
                        arguments={"path": f"evidence-{i}-{k}.txt"},
                    )
                    for k in range(3)
                ],
                finish_reason="tool_calls",
                usage=UsageInfo(input_tokens=80, output_tokens=20),
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text=f"Pass {i} gathered evidence.",
                finish_reason="stop",
                finalization_status={
                    "status": "final_answer",
                    "reasoning": "Evidence collected.",
                },
                usage=UsageInfo(input_tokens=80, output_tokens=20),
            ),
        ]
        runtime = _FakeRuntime(responses=responses)
        loop_ctx = _LoopContext(
            state=child,
            outcomes=[
                CommandExecutionOutcome(
                    approved_command=SimpleNamespace(),
                    action_result=ActionResult(
                        command_id=f"p{i}-{k}",
                        status="success",
                        summary=f"FACT_{i}_{k}=confirmed",
                        outputs={"content": f"FACT_{i}_{k}=confirmed"},
                    ),
                )
                for k in range(3)
            ],
        )
        allocation = child.budgets_remaining.tool_calls
        initial_llm = child.llm_calls_used
        initial_tokens = child.budgets_remaining.tokens
        outcome = run_adaptive_tool_loop(
            loop_ctx,
            profile=_profile(allowed_tools=frozenset({"file.read"}), max_iterations=4),
            runtime=runtime,
            model="fake-model",
            initial_messages=[Message(role="user", content=kwargs["user_input"])],
            tool_specs=_tool_specs("file.read"),
        )
        runs.append(
            {
                "allocation": allocation,
                "executed": len(loop_ctx.commands),
                "child_remaining_tools": child.budgets_remaining.tool_calls,
                "parent_remaining_tools": ctx.state.budgets_remaining.tool_calls,
                "child_llm_calls": child.llm_calls_used - initial_llm,
                "tokens": initial_tokens - child.budgets_remaining.tokens,
                "termination": outcome.termination_reason,
            }
        )
        tool_results = outcome.state.scratchpad.get("adaptive.tool_results", [])
        action = ActionResult(
            command_id=f"child-{i}",
            status="success",
            summary=outcome.final_text or "Evidence collected.",
            outputs={"tool_results": tool_results},
        )
        return SimpleNamespace(
            status="done",
            message=action.summary,
            action_result=action,
            working_state=child,
        )

    monkeypatch.setattr(
        "openminion.modules.brain.loop.strategies.research.handler.invoke_decision_direct",
        invoke_child,
    )
    result = mode.execute(ctx)
    print("real adaptive child runs:", runs)
    total = sum(r["executed"] for r in runs)
    print(
        "executed_total:",
        total,
        "parent_tools_remaining:",
        ctx.state.budgets_remaining.tool_calls,
        "parent_llm_calls:",
        ctx.state.llm_calls_used,
        "status:",
        result.status,
    )
    assert len(runs) <= 3
    assert ctx.state.budgets_remaining.tool_calls == parent_tool_budget - total
    assert ctx.state.llm_calls_used == sum(r["child_llm_calls"] for r in runs) + 1
    assert ctx.state.budgets_remaining.tokens == 6000 - sum(r["tokens"] for r in runs)
    assert total <= parent_tool_budget
    from openminion.services.brain.post_execution.postprocess_sources import (
        _cumulative_tool_results_from_step_output,
    )

    records = _cumulative_tool_results_from_step_output(result, parent_records)
    assert len(records) == total + len(parent_records)
    assert total == parent_tool_budget
    if reuse_call_ids:
        assert sum(item["call_id"] == "read-0" for item in records) == len(runs) + len(
            parent_records
        )
    # Serialized/replayed copies retain execution provenance and count once.
    import json

    replay = json.loads(json.dumps(records))
    assert _cumulative_tool_results_from_step_output(result, replay * 2) == records


HANDLER = (
    "openminion.modules.brain.loop.strategies.research.handler.invoke_decision_direct"
)


def _records(state, *, prefix="child"):
    return [
        {
            "call_id": f"{prefix}-{i}",
            "tool_name": "file.read",
            "ok": ok,
            "content": "local evidence",
            "turn_scope_id": state.trace_id,
            **({"error_code": "policy_denied"} if i == 2 else {}),
        }
        for i, ok in enumerate([True, False, False])
    ]


def _consume(child):
    child.budgets_remaining.tool_calls -= 3
    child.budgets_remaining.tokens -= 11
    child.budgets_remaining.ticks -= 1
    child.budgets_remaining.time_ms -= 7
    child.budgets_remaining.a2a_calls -= 1
    child.llm_calls_used += 2


@pytest.mark.parametrize("exit_kind", ["success", "partial", "exception", "cancel"])
def test_child_settles_all_exit_paths_without_merging_state(
    tmp_path, monkeypatch, exit_kind
):
    tm = TaskManager.for_lifecycle_db(db_path=tmp_path / "tasks.db")
    ctx, _ = _ctx(tm)
    ctx.state.llm_calls_used = 2
    inherited = _records(ctx.state, prefix="parent")[:1]
    ctx.state.module_state["adaptive_loop"] = {"tool_results": inherited}
    before = ctx.state.budgets_remaining.model_copy(deep=True)
    children = []

    def invoke(runner, **kwargs):
        child = kwargs["state"]
        children.append(child)
        assert "adaptive_loop" not in child.module_state
        assert child.module_state["research"]["child"] is True
        assert child.module_state["research"]["invocation_id"]
        _consume(child)
        child.goal = "private child goal"
        child.module_state["private"] = {"secret": "must not cross boundary"}
        child.module_state["adaptive_loop"] = {"tool_results": _records(child)}
        if exit_kind == "exception":
            raise RuntimeError("controlled failure after consumption")
        if exit_kind == "cancel":
            raise CancelledError()
        action = ActionResult(
            command_id="done",
            status="success",
            summary="evidence",
            outputs={"tool_results": _records(child) * 2},
        )
        return SimpleNamespace(
            status="done" if exit_kind == "success" else "waiting_user",
            message="evidence",
            action_result=action,
            working_state=child,
        )

    monkeypatch.setattr(HANDLER, invoke)
    mode = _make_mode(max_iterations=1)
    if exit_kind == "cancel":
        with pytest.raises(CancelledError):
            mode._execute_search_iteration(
                ctx, iteration=0, query="evidence", findings_so_far=[]
            )
    else:
        mode._execute_search_iteration(
            ctx, iteration=0, query="evidence", findings_so_far=[]
        )
    assert len(children) == 1
    assert ctx.state.budgets_remaining == BudgetCounters(
        ticks=before.ticks - 1,
        tool_calls=before.tool_calls - 3,
        a2a_calls=before.a2a_calls - 1,
        tokens=before.tokens - 11,
        time_ms=before.time_ms - 7,
    )
    assert ctx.state.llm_calls_used == 4
    assert ctx.state.goal != "private child goal"
    assert "private" not in ctx.state.module_state
    assert ctx.state.module_state["adaptive_loop"]["tool_results"] == inherited
    records = _cumulative_tool_results_from_step_output(
        SimpleNamespace(working_state=ctx.state), []
    )
    metadata = {"tool_calls_count": "0", "elapsed_ms": "1"}
    _attach_tool_result_metadata(
        None,
        metadata=metadata,
        tool_results_payload=records,
        termination_reason="model_final",
    )
    stats = RunStats.from_mapping(metadata)
    assert stats.tool_calls == 4
    assert (
        stats.tool_errors == 2
    )  # failed and policy-blocked terminal results both count
    children[0].module_state["adaptive_loop"]["tool_results"][0]["ok"] = False
    assert records[1]["ok"] is True


@pytest.mark.parametrize(
    "exhausted", ["ticks", "tool_calls", "tokens", "time_ms", "llm"]
)
def test_no_child_allocation_when_parent_exhausted(tmp_path, monkeypatch, exhausted):
    tm = TaskManager.for_lifecycle_db(db_path=tmp_path / "tasks.db")
    ctx, services = _ctx(tm)
    if exhausted == "llm":
        ctx.state.llm_calls_used = ctx.state.llm_calls_max
    else:
        setattr(ctx.state.budgets_remaining, exhausted, 0)
    before = ctx.state.budgets_remaining.model_copy(deep=True)
    monkeypatch.setattr(
        HANDLER, lambda *a, **kw: pytest.fail("exhausted parent allocated child")
    )
    result = _make_mode().execute(ctx)
    assert result.status == "waiting_user"
    assert ctx.state.budgets_remaining == before
    assert not services.runner.llm_api.calls


@pytest.mark.parametrize("status", ["complete", "incomplete"])
def test_success_and_incomplete_closeout_include_each_sibling_once(
    tmp_path, monkeypatch, status
):
    tm = TaskManager.for_lifecycle_db(db_path=tmp_path / "tasks.db")
    ctx, _ = _ctx(
        tm,
        synthesis_payload={
            "answer": "evidence",
            "status": status,
            "remaining_work": "" if status == "complete" else "more evidence",
        },
    )
    calls = []

    def invoke(runner, **kwargs):
        child = kwargs["state"]
        records = _records(child, prefix=str(len(calls)))[:1]
        calls.append(child)
        child.budgets_remaining.tool_calls -= 1
        action = ActionResult(
            command_id="done",
            status="success",
            summary="evidence",
            outputs={"tool_results": records},
        )
        return SimpleNamespace(
            status="done", message="evidence", action_result=action, working_state=child
        )

    monkeypatch.setattr(HANDLER, invoke)
    result = _make_mode(max_iterations=3).execute(ctx)
    assert len(calls) == 3
    assert len(_cumulative_tool_results_from_step_output(result, [])) == 3
    assert ctx.state.budgets_remaining.tool_calls == 7
    assert result.status == ("done" if status == "complete" else "waiting_user")


def test_resume_does_not_redebit_completed_iterations(tmp_path, monkeypatch):
    from openminion.modules.brain.schemas import WorkingState

    tm = TaskManager.for_lifecycle_db(db_path=tmp_path / "tasks.db")
    ctx, _ = _ctx(tm)
    ctx.state.budgets_remaining.ticks = 1
    calls = []

    def invoke(runner, **kwargs):
        child = kwargs["state"]
        records = _records(child, prefix=str(len(calls)))[:1]
        calls.append(child)
        child.budgets_remaining.tool_calls -= 1
        child.budgets_remaining.tokens -= 10
        child.llm_calls_used += 1
        action = ActionResult(
            command_id="done",
            status="success",
            summary="evidence",
            outputs={"tool_results": records},
        )
        return SimpleNamespace(
            status="done", message="evidence", action_result=action, working_state=child
        )

    monkeypatch.setattr(HANDLER, invoke)
    mode = _make_mode(max_iterations=3)
    assert mode.execute(ctx).status == "waiting_user"
    assert len(calls) == 1
    assert ctx.state.budgets_remaining.tool_calls == 9
    # Persist/reload state and the canonical task checkpoint; settlement has
    # already happened and is not part of the resumed findings payload.
    state = WorkingState.model_validate_json(ctx.state.model_dump_json())
    state.trace_id = "resumed-turn"
    state.budgets_remaining.ticks = 20
    resumed, _ = _ctx(tm, state=state)
    fresh_mode = _make_mode(max_iterations=3)
    resumed.state.task_backed_resume_state = fresh_mode.resume(
        resumed, state.task_backed_checkpoint_id
    )
    assert resumed.state.budgets_remaining.tool_calls == 9
    fresh_mode.execute(resumed)
    assert len(calls) == 3
    assert resumed.state.budgets_remaining.tool_calls == 7
    assert resumed.state.budgets_remaining.tokens == 4970
    assert resumed.state.llm_calls_used == 4  # three children and one synthesis
    assert (
        len(
            _cumulative_tool_results_from_step_output(
                SimpleNamespace(working_state=resumed.state), []
            )
        )
        == 2
    )


def test_parent_only_and_prior_turn_records_keep_existing_contract():
    from tests.brain.modes.test_research_contract import _state

    state = _state()
    records = _records(state)[:1]
    state.module_state["adaptive_loop"] = {"tool_results": records * 2}
    state.module_state["research"] = {
        "tool_results": [
            {**records[0], "call_id": "old", "turn_scope_id": "previous-turn"}
        ]
    }
    step = SimpleNamespace(working_state=state)
    assert _cumulative_tool_results_from_step_output(step, records) == records


@pytest.mark.parametrize("child_scope, expected", [("current", 3), ("previous", 1)])
def test_current_child_records_are_inclusive_but_stale_records_do_not_change_parent_contract(
    child_scope, expected
):
    from functools import partial
    from tests.brain.modes.test_research_contract import _state
    from openminion.services.brain.post_execution.postprocess_response import (
        _attach_postprocess_action_metadata,
    )

    state = _state()
    parent = _records(state, prefix="parent")[:1]
    child = _records(state, prefix="child")[:2]
    if child_scope == "previous":
        child = [{**item, "turn_scope_id": "previous-turn"} for item in child]
    state.module_state["adaptive_loop"] = {"tool_results": parent}
    state.module_state["research"] = {"tool_results": child}
    metadata = {}
    _attach_postprocess_action_metadata(
        SimpleNamespace(
            _attach_tool_result_metadata=partial(_attach_tool_result_metadata, None)
        ),
        runner=SimpleNamespace(profile=SimpleNamespace(structural_tool_results=False)),
        metadata=metadata,
        step_out=SimpleNamespace(working_state=state, action_result=None),
        tool_results_payload=parent,
        termination_reason="model_final",
        finalization_payload=None,
    )
    assert int(metadata["tool_calls_count"]) == expected
    assert RunStats.from_mapping(metadata).tool_errors == (
        1 if child_scope == "current" else 0
    )
