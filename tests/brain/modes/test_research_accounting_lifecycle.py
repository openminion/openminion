"""Concurrent turns and exceptional child snapshot coverage."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

from openminion.modules.brain.loop.tools import run_adaptive_tool_loop
from openminion.modules.brain.schemas import ActionResult
from openminion.modules.brain.tools.executor import CommandExecutionOutcome
from openminion.modules.llm.schemas import LLMResponse, Message, ToolCall, UsageInfo
from openminion.modules.task import TaskManager
from openminion.services.brain.post_execution.postprocess_sources import (
    _cumulative_tool_results_from_step_output,
)
from tests.brain.modes.test_research_contract import _ctx, _make_mode, _state
from tests.brain.tool_loops.test_engine import (
    _FakeRuntime,
    _LoopContext,
    _profile,
    _tool_specs,
)

HANDLER = (
    "openminion.modules.brain.loop.strategies.research.handler.invoke_decision_direct"
)


def test_exception_after_long_child_keeps_all_completed_results(tmp_path, monkeypatch):
    tm = TaskManager.for_lifecycle_db(db_path=tmp_path / "tasks.db")
    ctx, _ = _ctx(tm)
    ctx.state.budgets_remaining.tool_calls = 40
    dispatched = []

    def invoke(runner, **kwargs):
        child = kwargs["state"]
        runtime = _FakeRuntime(
            responses=[
                LLMResponse(
                    ok=True,
                    provider="fake",
                    model="fake-model",
                    finish_reason="tool_calls",
                    tool_calls=[
                        ToolCall(
                            id=f"call-{i}",
                            name="file.read",
                            arguments={"path": f"{i}.txt"},
                        )
                        for i in range(30)
                    ],
                    usage=UsageInfo(input_tokens=8, output_tokens=2),
                )
            ]
        )
        loop = _LoopContext(
            state=child,
            outcomes=[
                CommandExecutionOutcome(
                    approved_command=SimpleNamespace(),
                    action_result=ActionResult(
                        command_id=f"command-{i}",
                        status="success",
                        summary="evidence",
                        outputs={"content": "local evidence"},
                    ),
                )
                for i in range(30)
            ],
        )
        try:
            # The second completion raises IndexError after the batch's durable
            # loop snapshot, before any final child ActionResult is returned.
            run_adaptive_tool_loop(
                loop,
                profile=_profile(allowed_tools=frozenset({"file.read"})),
                runtime=runtime,
                model="fake-model",
                initial_messages=[Message(role="user", content="read evidence")],
                tool_specs=_tool_specs("file.read"),
            )
        finally:
            dispatched.extend(loop.commands)

    monkeypatch.setattr(HANDLER, invoke)
    _make_mode(max_iterations=1)._execute_search_iteration(
        ctx, iteration=0, query="evidence", findings_so_far=[]
    )
    assert len(dispatched) == 30
    assert ctx.state.budgets_remaining.tool_calls == 10
    records = _cumulative_tool_results_from_step_output(
        SimpleNamespace(working_state=ctx.state), []
    )
    assert len(records) == 30


def test_concurrent_research_has_independent_results_and_remaining_budgets(
    tmp_path, monkeypatch
):
    barrier = Barrier(2)

    def invoke(runner, **kwargs):
        child = kwargs["state"]
        barrier.wait(timeout=10)
        child.budgets_remaining.tool_calls -= 1
        child.budgets_remaining.tokens -= 10
        child.llm_calls_used += 1
        record = {
            "call_id": "same-provider-id",
            "tool_name": "file.read",
            "ok": True,
            "turn_scope_id": child.trace_id,
            "content": child.session_id,
        }
        action = ActionResult(
            command_id="done",
            status="success",
            summary="evidence",
            outputs={"tool_results": [record]},
        )
        return SimpleNamespace(
            status="done", message="evidence", action_result=action, working_state=child
        )

    monkeypatch.setattr(HANDLER, invoke)

    def run(index):
        tm = TaskManager.for_lifecycle_db(db_path=tmp_path / f"{index}.db")
        ctx, _ = _ctx(tm, state=_state(session_id=f"session-{index}"))
        ctx.state.budgets_remaining.tool_calls = 3 + index
        ctx.state.llm_calls_used = index
        result = _make_mode(max_iterations=1).execute(ctx)
        return ctx, _cumulative_tool_results_from_step_output(result, [])

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, [0, 1]))
    for index, (ctx, records) in enumerate(results):
        assert ctx.state.budgets_remaining.tool_calls == 2 + index
        assert ctx.state.budgets_remaining.tokens == 4990
        assert ctx.state.llm_calls_used == index + 2
        assert len(records) == 1
        assert records[0]["content"] == f"session-{index}"


def test_cancellation_before_iteration_snapshot_settles_terminal_fact(
    tmp_path, monkeypatch
):
    from asyncio import CancelledError
    import pytest

    tm = TaskManager.for_lifecycle_db(db_path=tmp_path / "tasks.db")
    ctx, _ = _ctx(tm)
    before = ctx.state.budgets_remaining.tool_calls

    def invoke(runner, **kwargs):
        child = kwargs["state"]
        loop = _LoopContext(
            state=child,
            outcomes=[
                CommandExecutionOutcome(
                    approved_command=SimpleNamespace(),
                    action_result=ActionResult(
                        command_id="done", status="success", summary="evidence"
                    ),
                )
            ],
        )
        runtime = _FakeRuntime(
            responses=[
                LLMResponse(
                    ok=True,
                    provider="fake",
                    model="fake-model",
                    finish_reason="tool_calls",
                    tool_calls=[
                        ToolCall(
                            id="read",
                            name="file.read",
                            arguments={"path": "evidence.txt"},
                        )
                    ],
                )
            ]
        )

        def cancel(loop_state):
            raise CancelledError()

        run_adaptive_tool_loop(
            loop,
            profile=_profile(allowed_tools=frozenset({"file.read"})),
            runtime=runtime,
            model="fake-model",
            initial_messages=[Message(role="user", content="read evidence")],
            tool_specs=_tool_specs("file.read"),
            on_tool_result=cancel,
        )

    monkeypatch.setattr(HANDLER, invoke)
    with pytest.raises(CancelledError):
        _make_mode(max_iterations=1)._execute_search_iteration(
            ctx, iteration=0, query="evidence", findings_so_far=[]
        )
    assert ctx.state.budgets_remaining.tool_calls == before - 1
    records = _cumulative_tool_results_from_step_output(
        SimpleNamespace(working_state=ctx.state), []
    )
    assert [r["call_id"] for r in records] == ["read"]
