from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from openminion.modules.brain.loop.tools.parallel import execute_parallel_tool_batch
from openminion.modules.brain.loop.tools.contracts import (
    AdaptiveToolLoopProfile,
    CommandExecutionOutcome,
    PreparedToolDispatch,
    PrepareOutcome,
    RawToolResult,
)
from openminion.modules.brain.schemas import ActionResult, ToolCommand


# Fake infrastructure


@dataclass
class _FakeTool:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class _FakeOutcome:
    summary: str
    action_result: Any = None
    job: Any = None


@dataclass
class _FakeCtx:
    call_order: list[str] = field(default_factory=list)
    state: Any = None

    def execute_command(
        self, *, command, include_reflect: bool = False
    ) -> _FakeOutcome:
        tool_name = str(getattr(command, "tool_name", "") or "")
        self.call_order.append(tool_name)
        return _FakeOutcome(summary=f"result of {tool_name}")

    def emit_status(self, **_: Any) -> None:
        pass


@dataclass
class _PreparedCtx:
    prepared_calls: list[str] = field(default_factory=list)
    worker_calls: list[str] = field(default_factory=list)
    finalized_calls: list[str] = field(default_factory=list)
    immediate_calls: list[str] = field(default_factory=list)
    state: Any = None

    def prepare_tool_dispatch(
        self,
        *,
        command,
        include_reflect: bool = False,
    ) -> PreparedToolDispatch | PrepareOutcome:
        del include_reflect
        tool_name = str(getattr(command, "tool_name", "") or "")
        self.prepared_calls.append(tool_name)
        if tool_name == "weather":
            return PrepareOutcome(
                approved_command=command,
                original_command=command,
                command_id="cmd-weather",
                tool_name=tool_name,
                disposition="ask_user",
                action_result=ActionResult(
                    command_id="cmd-weather",
                    status="needs_user",
                    summary="need approval",
                ),
            )
        return PreparedToolDispatch(
            approved_command=command,
            original_command=command,
            command_id=f"cmd-{tool_name}",
            tool_name=tool_name,
            validated_args=dict(getattr(command, "args", {}) or {}),
            session_id="s",
            trace_id="t",
            agent_id="a",
            lineage={},
            permission_mode="ask",
            payload={"tool_name": tool_name},
        )

    def execute_prepared_tool_dispatch(self, *, prepared_dispatch) -> RawToolResult:
        self.worker_calls.append(prepared_dispatch.tool_name)
        return RawToolResult(
            command_id=prepared_dispatch.command_id,
            tool_name=prepared_dispatch.tool_name,
            raw_output={"summary": f"ran {prepared_dispatch.tool_name}"},
        )

    def finalize_tool_result(
        self,
        *,
        prepared_dispatch,
        raw_result,
    ) -> CommandExecutionOutcome:
        self.finalized_calls.append(prepared_dispatch.tool_name)
        return CommandExecutionOutcome(
            approved_command=prepared_dispatch.approved_command,
            action_result=ActionResult(
                command_id=raw_result.command_id,
                status="success",
                summary=str(raw_result.raw_output.get("summary", "")),
            ),
        )

    def finalize_prepare_outcome(
        self,
        *,
        prepare_outcome,
    ) -> CommandExecutionOutcome:
        self.immediate_calls.append(prepare_outcome.tool_name)
        return CommandExecutionOutcome(
            approved_command=prepare_outcome.approved_command,
            action_result=prepare_outcome.action_result,
        )

    def emit_status(self, **_: Any) -> None:
        pass


def _read_tool(path: str) -> _FakeTool:
    return _FakeTool(name="file.read", arguments={"path": path})


def _independent_reads(count: int) -> list[_FakeTool]:
    return [_read_tool(f"/src/file{i}.py") for i in range(count)]


# Test 1: Capacity=1 serializes all independent reads


def test_capacity_1_serializes_all() -> None:
    ctx = _FakeCtx()
    tools = _independent_reads(3)

    result = execute_parallel_tool_batch(
        loop_ctx=ctx,
        tool_calls=tools,
        include_reflect=False,
        provider_parallel_tool_capacity=1,
    )

    # With capacity=1 all calls go sequential, no parallel fan-out
    assert result.parallel_fan_out_count == 0
    assert result.tool_calls_sequential == 3
    assert result.tool_calls_parallel == 0
    assert len(result.ordered_results) == 3


# Test 2: Capacity=2 sub-batches a group of 4 into two parallel runs of 2


def test_capacity_2_sub_batches_group_of_4() -> None:
    ctx = _FakeCtx()
    tools = _independent_reads(4)

    result = execute_parallel_tool_batch(
        loop_ctx=ctx,
        tool_calls=tools,
        include_reflect=False,
        provider_parallel_tool_capacity=2,
    )

    # Two sub-batches of 2 → 2 fan-out events, 4 parallel calls
    assert result.parallel_fan_out_count == 2
    assert result.tool_calls_parallel == 4
    assert result.tool_calls_sequential == 0
    assert len(result.ordered_results) == 4


# Test 3: Default capacity (0) preserves unlimited parallelism


def test_default_capacity_unlimited_preserves_parallel() -> None:
    ctx = _FakeCtx()
    tools = _independent_reads(3)

    result = execute_parallel_tool_batch(
        loop_ctx=ctx,
        tool_calls=tools,
        include_reflect=False,
        provider_parallel_tool_capacity=0,  # unlimited
    )

    # Should fan out as a single group of 3
    assert result.parallel_fan_out_count == 1
    assert result.tool_calls_parallel == 3
    assert result.tool_calls_sequential == 0


def test_seeded_tool_command_preserves_confirmation_replay_inputs() -> None:
    captured_inputs: list[dict[str, Any]] = []

    @dataclass
    class _CaptureCtx(_FakeCtx):
        def execute_command(
            self, *, command, include_reflect: bool = False
        ) -> _FakeOutcome:
            captured_inputs.append(dict(getattr(command, "inputs", {}) or {}))
            return super().execute_command(
                command=command,
                include_reflect=include_reflect,
            )

    ctx = _CaptureCtx()
    command = ToolCommand(
        title="write file",
        tool_name="file.write",
        args={"path": "README.md", "content": "ok"},
        inputs={
            "confirmation_source": "policy_replay",
            "confirmation_grant_id": "grant-123",
        },
    )

    result = execute_parallel_tool_batch(
        loop_ctx=ctx,
        tool_calls=[command],
        include_reflect=False,
        provider_parallel_tool_capacity=1,
    )

    assert result.tool_calls_sequential == 1
    assert captured_inputs == [
        {
            "confirmation_source": "policy_replay",
            "confirmation_grant_id": "grant-123",
        }
    ]


# Test 4: Profile field defaults to 1 (serial guardrail)


def test_profile_default_capacity_is_serial_guardrail() -> None:
    profile = AdaptiveToolLoopProfile(
        profile_name="test",
        mode_name="act",
        tool_exposure_policy="explicit_allowlist",
        allowed_tools=frozenset({"tool_a"}),
    )
    assert profile.provider_parallel_tool_capacity == 1


# Test 5: Capacity=3 allows a group of 3 to fully parallelize


def test_capacity_3_allows_full_parallel_for_group_of_3() -> None:
    ctx = _FakeCtx()
    tools = _independent_reads(3)

    result = execute_parallel_tool_batch(
        loop_ctx=ctx,
        tool_calls=tools,
        include_reflect=False,
        provider_parallel_tool_capacity=3,
    )

    assert result.parallel_fan_out_count == 1
    assert result.tool_calls_parallel == 3
    assert result.tool_calls_sequential == 0


# Test 6: Ordered results preserve input index order regardless of capacity


def test_ordered_results_preserve_index_order() -> None:
    ctx = _FakeCtx()
    tools = _independent_reads(4)

    result = execute_parallel_tool_batch(
        loop_ctx=ctx,
        tool_calls=tools,
        include_reflect=False,
        provider_parallel_tool_capacity=2,
    )

    assert len(result.ordered_results) == len(tools)
    for i, (tc, outcome) in enumerate(result.ordered_results):
        assert tc is tools[i]


def test_prepare_outcome_bypasses_worker_pool_and_preserves_order() -> None:
    ctx = _PreparedCtx()
    tools = [
        _FakeTool(name="weather", arguments={"location": "SF"}),
        _read_tool("/src/file1.py"),
    ]

    result = execute_parallel_tool_batch(
        loop_ctx=ctx,
        tool_calls=tools,
        include_reflect=False,
        provider_parallel_tool_capacity=2,
    )

    assert ctx.prepared_calls == ["weather", "file.read"]
    assert ctx.worker_calls == ["file.read"]
    assert ctx.immediate_calls == ["weather"]
    assert ctx.finalized_calls == ["file.read"]
    assert [tc.name for tc, _ in result.ordered_results] == ["weather", "file.read"]
