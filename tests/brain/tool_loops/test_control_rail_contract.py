from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openminion.modules.brain.loop.tools.direct_tool import (
    _build_direct_tool_closure_message,
    _restore_direct_tool_specs_after_shortlist,
    _visible_tool_specs_for_direct_tool_turn,
)
from openminion.modules.brain.loop.tools.engine import (
    _build_tool_failure_recovery_message,
    _duplicate_batch_recovery_message,
)
from openminion.modules.brain.loop.tools.contracts import (
    AdaptiveToolLoopState,
    DirectToolTurnContext,
)
from openminion.modules.llm.schemas import ToolSpec


_BANNED_NEXT_STEP_PHRASES = (
    "answer the user",
    "answer the user directly",
    "choose a different next step",
    "ask the user for the missing value",
    "retry with corrected arguments",
    "call the tool again with corrected arguments",
    "use the existing tool results",  # case-insensitive check below
)


def _assert_no_next_step_prose(content: str) -> None:
    lowered = content.lower()
    for phrase in _BANNED_NEXT_STEP_PHRASES:
        assert phrase.lower() not in lowered, (
            f"Rail content includes banned next-step phrase "
            f"{phrase!r}; ALCR contract violated."
        )


@dataclass
class _FakeActionError:
    message: str
    code: str = ""


@dataclass
class _FakeActionResult:
    status: str
    error: _FakeActionError | None = None
    summary: str = ""


class TestFailureRecoveryContract:
    def test_exact_content_on_failed_action(self) -> None:
        action = _FakeActionResult(
            status="failed",
            error=_FakeActionError(message="missing city argument", code="E_INPUT"),
        )
        msg = _build_tool_failure_recovery_message(
            tool_name="weather.lookup", action_result=action
        )
        assert msg is not None
        assert msg.content == (
            "The previous weather.lookup tool call failed (code=E_INPUT): "
            "missing city argument Do not repeat the same invalid call."
        )

    def test_exact_content_without_error_code(self) -> None:
        action = _FakeActionResult(
            status="failed",
            error=_FakeActionError(message="tool crashed"),
        )
        msg = _build_tool_failure_recovery_message(
            tool_name="weather.lookup", action_result=action
        )
        assert msg is not None
        assert msg.content == (
            "The previous weather.lookup tool call failed: "
            "tool crashed Do not repeat the same invalid call."
        )

    def test_absence_of_next_step_prose(self) -> None:
        action = _FakeActionResult(
            status="failed",
            error=_FakeActionError(message="boom", code="E"),
        )
        msg = _build_tool_failure_recovery_message(
            tool_name="any.tool", action_result=action
        )
        assert msg is not None
        _assert_no_next_step_prose(msg.content)

    def test_direct_tool_shortlist_restores_requested_tool_schema(self) -> None:
        state = AdaptiveToolLoopState(
            direct_tool_turn=DirectToolTurnContext(
                requested_tool_names=("mcp.fixture.echo_text",),
                requested_batch_signature="mcp.fixture.echo_text:{text}",
            )
        )
        active_specs = [ToolSpec(name="exec.run", description="", input_schema={})]
        requestable_specs = [
            *active_specs,
            ToolSpec(
                name="mcp.fixture.echo_text",
                description="MCP echo fixture",
                input_schema={},
            ),
        ]

        restored = _restore_direct_tool_specs_after_shortlist(
            loop_state=state,
            active_tool_specs=active_specs,
            requestable_tool_specs=requestable_specs,
        )

        assert [spec.name for spec in restored] == ["exec.run", "mcp.fixture.echo_text"]
        assert state.scratchpad["direct_tool_shortlist_restored_tools"] == [
            "mcp.fixture.echo_text"
        ]
        assert [spec.name for spec in _visible_tool_specs_for_direct_tool_turn(state, restored)] == [
            "mcp.fixture.echo_text"
        ]

    def test_retains_fact_and_hard_constraint(self) -> None:
        action = _FakeActionResult(
            status="failed",
            error=_FakeActionError(message="boom", code="E"),
        )
        msg = _build_tool_failure_recovery_message(
            tool_name="any.tool", action_result=action
        )
        assert msg is not None
        assert "any.tool" in msg.content
        assert "boom" in msg.content
        assert "Do not repeat the same invalid call." in msg.content

    def test_timeout_status_also_emits_rail(self) -> None:
        action = _FakeActionResult(
            status="timeout",
            error=_FakeActionError(message="slow", code="E_TIMEOUT"),
        )
        msg = _build_tool_failure_recovery_message(
            tool_name="slow.tool", action_result=action
        )
        assert msg is not None
        _assert_no_next_step_prose(msg.content)


class TestDuplicateBatchRecoveryContract:
    def _fake_tool_calls(self, names: list[str]) -> list[Any]:
        @dataclass
        class _FakeCall:
            name: str

        return [_FakeCall(name=n) for n in names]

    def test_exact_content_on_named_tools(self) -> None:
        msg = _duplicate_batch_recovery_message(
            self._fake_tool_calls(["web.search", "web.fetch"])
        )
        assert msg.content == (
            "The tool batch (web.search, web.fetch) was already executed with "
            "the same arguments and produced tool results in this loop. Do not "
            "repeat the same tool call with identical arguments unless the "
            "prior tool result explicitly instructed you to poll or retry with "
            "changed inputs."
        )

    def test_absence_of_next_step_prose(self) -> None:
        msg = _duplicate_batch_recovery_message(self._fake_tool_calls(["web.search"]))
        _assert_no_next_step_prose(msg.content)

    def test_retains_fact_and_conditional_constraint(self) -> None:
        msg = _duplicate_batch_recovery_message(self._fake_tool_calls(["web.search"]))
        assert "web.search" in msg.content
        assert "already executed" in msg.content
        assert "Do not repeat the same tool call" in msg.content
        assert (
            "unless the prior tool result explicitly instructed you to poll"
            in msg.content
        )


class TestDirectToolClosureContract:
    def _fake_loop_state(self, requested_tools: list[str] | None) -> Any:
        @dataclass
        class _FakeDirectTurn:
            requested_tool_names: tuple[str, ...] | None

        @dataclass
        class _FakeLoopState:
            direct_tool_turn: _FakeDirectTurn

        return _FakeLoopState(
            direct_tool_turn=_FakeDirectTurn(
                requested_tool_names=(
                    tuple(requested_tools) if requested_tools is not None else None
                )
            )
        )

    def test_exact_content_on_named_tools(self) -> None:
        state = self._fake_loop_state(["web.search", "web.fetch"])
        msg = _build_direct_tool_closure_message(state)
        assert msg.content == (
            "The explicit requested tool batch (web.search, web.fetch) "
            "already completed successfully for this turn. Do not call "
            "more tools."
        )

    def test_absence_of_next_step_prose(self) -> None:
        state = self._fake_loop_state(["web.search"])
        msg = _build_direct_tool_closure_message(state)
        _assert_no_next_step_prose(msg.content)

    def test_retains_fact_and_hard_constraint(self) -> None:
        state = self._fake_loop_state(["web.search"])
        msg = _build_direct_tool_closure_message(state)
        assert "web.search" in msg.content
        assert "already completed successfully" in msg.content
        assert "Do not call more tools." in msg.content
