from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from openminion.modules.brain.schemas import (
    ActionError,
    ActionResult,
    BudgetCounters,
    WorkingState,
    new_uuid,
)
from openminion.modules.brain.loop.tools import (
    ADAPTIVE_TERM_FINAL_TEXT,
    AdaptiveToolLoopProfile,
    run_adaptive_tool_loop,
)
from openminion.modules.brain.tools.executor import CommandExecutionOutcome
from openminion.modules.llm.schemas import (
    LLMResponse,
    Message,
    ToolCall,
    ToolSpec,
)


@dataclass
class _FakeRuntime:
    responses: list[LLMResponse] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    _index: int = 0

    def complete(
        self,
        *,
        messages,
        tools,
        model,
        tool_choice="auto",
        max_output_tokens=None,
        metadata=None,
    ):
        self.calls.append(
            {
                "messages": [
                    Message(role=msg.role, content=msg.content) for msg in messages
                ],
            }
        )
        response = self.responses[self._index]
        self._index += 1
        return response


@dataclass
class _LoopContext:
    state: WorkingState
    outcomes: list[CommandExecutionOutcome] = field(default_factory=list)
    commands: list[Any] = field(default_factory=list)
    statuses: list[dict[str, Any]] = field(default_factory=list)
    session_api: Any | None = None
    _index: int = 0

    def execute_command(self, *, command, include_reflect: bool = False):
        del include_reflect
        self.commands.append(command)
        outcome = self.outcomes[self._index]
        self._index += 1
        return outcome

    def emit_status(self, **kwargs) -> None:
        self.statuses.append(dict(kwargs))


def _state() -> WorkingState:
    return WorkingState(
        session_id="s-ptfi",
        agent_id="agent",
        budgets_remaining=BudgetCounters(
            ticks=10,
            tool_calls=5,
            a2a_calls=0,
            tokens=5000,
            time_ms=120000,
        ),
        llm_calls_max=5,
    )


def _tool_specs(*names: str) -> list[ToolSpec]:
    return [
        ToolSpec(
            name=name,
            description=name,
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": True,
            },
        )
        for name in names
    ]


def _profile(allowed_tools: frozenset[str]) -> AdaptiveToolLoopProfile:
    return AdaptiveToolLoopProfile(
        profile_name="ptfi_regression",
        mode_name="act_adaptive",
        allowed_tools=allowed_tools,
        max_iterations=4,
        tool_choice="auto",
        provider_parallel_tool_capacity=1,
    )


def _success_outcome(tool_name: str, summary: str) -> CommandExecutionOutcome:
    return CommandExecutionOutcome(
        approved_command=SimpleNamespace(tool_name=tool_name, args={"path": "x"}),
        action_result=ActionResult(
            command_id=new_uuid(),
            status="success",
            summary=summary,
            outputs={"content": summary},
        ),
    )


def _validation_failure_outcome(tool_name: str) -> CommandExecutionOutcome:
    return CommandExecutionOutcome(
        approved_command=SimpleNamespace(tool_name=tool_name, args={"command": []}),
        action_result=ActionResult(
            command_id=new_uuid(),
            status="failed",
            summary="Invalid tool arguments",
            error=ActionError(
                code="TOOL_ARG_VALIDATION_FAILED",
                message="command must be a string",
                details={
                    "validation_path": ["command"],
                    "schema": {"type": "string"},
                },
            ),
        ),
    )


def test_schema_error_and_active_plan_reach_model_owned_correction_turn() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                tool_calls=[
                    ToolCall(id="c1", name="exec.run", arguments={"command": []})
                ],
                finish_reason="tool_calls",
                assistant_messages=[],
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="c2",
                        name="exec.run",
                        arguments={"command": "python -m pytest -q"},
                    )
                ],
                finish_reason="tool_calls",
                assistant_messages=[],
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="Validation passed.",
                tool_calls=[],
                finish_reason="stop",
                assistant_messages=[],
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(),
        outcomes=[
            _validation_failure_outcome("exec.run"),
            _success_outcome("exec.run", "1 passed"),
        ],
    )
    tool_spec = ToolSpec(
        name="exec.run",
        description="Run a command",
        input_schema={
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
            "additionalProperties": False,
        },
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(frozenset({"exec.run"})),
        runtime=runtime,
        model="m",
        initial_messages=[
            Message(
                role="system",
                content=(
                    "[ACTIVE PLAN]\n"
                    "- validate [in_progress]: Run the validation command"
                ),
            ),
            Message(role="user", content="Continue the validation."),
        ],
        tool_specs=[tool_spec],
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert [command.args for command in loop_ctx.commands] == [
        {"command": []},
        {"command": "python -m pytest -q"},
    ]
    correction_context = "\n".join(
        str(message.content or "") for message in runtime.calls[1]["messages"]
    )
    assert "[ACTIVE PLAN]" in correction_context
    assert '"code": "TOOL_ARG_VALIDATION_FAILED"' in correction_context
    assert '"validation_path": ["command"]' in correction_context
    assert '"schema": {"type": "string"}' in correction_context


def test_coding_post_tool_followup_prose_attributed_as_assistant_role() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                tool_calls=[
                    ToolCall(id="c1", name="file.read", arguments={"path": "a"})
                ],
                finish_reason="tool_calls",
                assistant_messages=[],
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="## Project Overview: Task Summary CLI\n\nThis is a well-structured Python package.",
                tool_calls=[],
                finish_reason="stop",
                assistant_messages=[],
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(),
        outcomes=[_success_outcome("file.read", "{count:8}")],
    )
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(frozenset({"file.read"})),
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="explore the repo")],
        tool_specs=_tool_specs("file.read"),
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    final_messages = outcome.state.messages
    assistant_roles_with_prose = [
        m
        for m in final_messages
        if m.role == "assistant" and "Project Overview" in str(m.content or "")
    ]
    assert len(assistant_roles_with_prose) == 1, (
        "PTFI-03: prose-only response must enter loop_state.messages "
        "exactly once as role='assistant' even when assistant_messages "
        "was empty on the LLMResponse"
    )
    user_roles_with_prose = [
        m
        for m in final_messages
        if m.role == "user" and "Project Overview" in str(m.content or "")
    ]
    assert user_roles_with_prose == [], (
        "PTFI-03: prose-only response must NOT appear as role='user' in "
        "loop_state.messages — that was the downstream defect after the "
        "EDR confirmation-replay fix"
    )


def test_research_post_tool_followup_prose_attributed_as_assistant_role() -> None:
    research_prose = (
        "I can see this is a research project workspace with the "
        "following structure:\n\n**Directory:** `research-project-1779780196`"
    )
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="file.read",
                        arguments={"path": "workspace"},
                    )
                ],
                finish_reason="tool_calls",
                assistant_messages=[],
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text=research_prose,
                tool_calls=[],
                finish_reason="stop",
                assistant_messages=[],
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(),
        outcomes=[_success_outcome("file.read", "{count:5}")],
    )
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(frozenset({"file.read"})),
        runtime=runtime,
        model="m",
        initial_messages=[
            Message(role="user", content="summarise the research workspace")
        ],
        tool_specs=_tool_specs("file.read"),
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    final_messages = outcome.state.messages
    assistant_roles_with_prose = [
        m
        for m in final_messages
        if m.role == "assistant" and "research project" in str(m.content or "")
    ]
    assert len(assistant_roles_with_prose) == 1, (
        "PTFI-03: research prose-only response must enter "
        "loop_state.messages exactly once as role='assistant'"
    )
    user_roles_with_prose = [
        m
        for m in final_messages
        if m.role == "user" and "research project" in str(m.content or "")
    ]
    assert user_roles_with_prose == [], (
        "PTFI-03: research prose-only response must NOT appear as "
        "role='user' anywhere in loop_state.messages"
    )


def test_populated_assistant_messages_skip_ptfi_fallback() -> None:
    prose_text = "All set; final result below."
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text=prose_text,
                tool_calls=[],
                finish_reason="stop",
                assistant_messages=[Message(role="assistant", content=prose_text)],
            )
        ]
    )
    loop_ctx = _LoopContext(state=_state(), outcomes=[])
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(frozenset({"file.read"})),
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="finish up")],
        tool_specs=_tool_specs("file.read"),
    )
    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assistant_prose_messages = [
        m
        for m in outcome.state.messages
        if m.role == "assistant" and m.content == prose_text
    ]
    assert len(assistant_prose_messages) == 1, (
        "PTFI-03: when assistant_messages is already populated, the "
        "fallback owner must not duplicate the assistant message"
    )
