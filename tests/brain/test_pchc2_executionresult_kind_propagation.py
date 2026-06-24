from __future__ import annotations

from types import SimpleNamespace

from openminion.modules.brain.constants import (
    BRAIN_STATE_WAITING_USER,
    RESPOND_KIND_ASSISTANT,
    RESPOND_KIND_POLICY_CONFIRMATION_PROMPT,
)
from openminion.modules.brain.execution.loop_contracts import ExecutionResult
from openminion.modules.brain.schemas import (
    BudgetCounters,
    StepOutput,
    WorkingState,
)


def _state(*, pending_confirmation: bool = False) -> WorkingState:
    state = WorkingState(
        session_id="s-pchc2",
        agent_id="agent",
        budgets_remaining=BudgetCounters(
            ticks=4,
            tool_calls=4,
            a2a_calls=0,
            tokens=1000,
            time_ms=60_000,
        ),
        status="waiting_user",
    )
    if pending_confirmation:
        # The structural signal is the typed state field — we just
        # mark it non-None by stamping any object that the structural
        # check at modes.py:1690 reads via getattr.
        state.pending_confirmation_command = SimpleNamespace(
            tool_name="exec.run", args={"command": "pytest -q"}
        )
        state.post_action_user_message = (
            "Policy confirmation required.\n"
            "exec.run (command=pytest -q)\n"
            "Reply exactly yes to confirm or exactly no to cancel."
        )
    return state


# ExecutionResult.kind default + propagation through to/from StepOutput.


def test_execution_result_default_kind_is_assistant() -> None:
    state = _state()
    result = ExecutionResult(
        status=BRAIN_STATE_WAITING_USER,
        working_state=state,
        message="hi",
    )
    assert result.kind == RESPOND_KIND_ASSISTANT


def test_execution_result_to_step_output_propagates_kind() -> None:
    state = _state()
    result = ExecutionResult(
        status=BRAIN_STATE_WAITING_USER,
        working_state=state,
        message="Policy prompt",
        kind=RESPOND_KIND_POLICY_CONFIRMATION_PROMPT,
    )
    output = result.to_step_output()
    assert isinstance(output, StepOutput)
    assert output.kind == RESPOND_KIND_POLICY_CONFIRMATION_PROMPT


def test_execution_result_to_step_output_default_kind() -> None:
    state = _state()
    result = ExecutionResult(
        status=BRAIN_STATE_WAITING_USER,
        working_state=state,
        message="hi",
    )
    output = result.to_step_output()
    assert output.kind == RESPOND_KIND_ASSISTANT


def test_execution_result_from_step_output_reads_kind() -> None:
    state = _state()
    output = StepOutput(
        session_id=state.session_id,
        status=BRAIN_STATE_WAITING_USER,
        message="Policy prompt",
        working_state=state,
        kind=RESPOND_KIND_POLICY_CONFIRMATION_PROMPT,
    )
    result = ExecutionResult.from_step_output(output)
    assert result.kind == RESPOND_KIND_POLICY_CONFIRMATION_PROMPT


def test_execution_result_from_step_output_default_when_missing() -> None:
    state = _state()
    fake_output = SimpleNamespace(
        status=BRAIN_STATE_WAITING_USER,
        working_state=state,
        message="hi",
        action_result=None,
        # NB: no `kind` attribute at all on this duck-typed double.
    )
    result = ExecutionResult.from_step_output(fake_output)
    assert result.kind == RESPOND_KIND_ASSISTANT


# _result_from_needs_user structural routing.


def _build_needs_user_helper():
    from openminion.modules.brain.loop.adaptive.modes import ActLoopMode

    return ActLoopMode._result_from_needs_user


def _ctx(state: WorkingState) -> SimpleNamespace:
    return SimpleNamespace(state=state)


def _outcome(message: str = "") -> SimpleNamespace:
    action_result = SimpleNamespace(summary=message or "Approval required.")
    return SimpleNamespace(action_result=action_result)


def test_result_from_needs_user_default_kind_when_no_pending_confirmation() -> None:
    helper = _build_needs_user_helper()
    state = _state(pending_confirmation=False)
    state.post_action_user_message = "Please clarify the date range."
    result = helper(_ctx(state), outcome=_outcome("Please clarify the date range."))
    assert result.kind == RESPOND_KIND_ASSISTANT
    assert result.message == "Please clarify the date range."


def test_result_from_needs_user_typed_kind_when_pending_confirmation() -> None:
    helper = _build_needs_user_helper()
    state = _state(pending_confirmation=True)
    result = helper(_ctx(state), outcome=_outcome("ignored"))
    assert result.kind == RESPOND_KIND_POLICY_CONFIRMATION_PROMPT
    assert "Policy confirmation required" in (result.message or "")


def test_result_from_needs_user_default_when_pending_but_no_staged_message() -> None:
    helper = _build_needs_user_helper()
    state = _state(pending_confirmation=True)
    state.post_action_user_message = ""  # explicitly empty
    result = helper(_ctx(state), outcome=_outcome("fallback summary"))
    assert result.kind == RESPOND_KIND_ASSISTANT


def test_result_from_needs_user_status_is_waiting_user() -> None:
    helper = _build_needs_user_helper()
    state = _state(pending_confirmation=True)
    result = helper(_ctx(state), outcome=_outcome("ignored"))
    assert result.status == BRAIN_STATE_WAITING_USER


# ExecutionContext.respond + ModeServices.respond_with_meta accept kind.


def test_execution_context_respond_threads_kind_to_services() -> None:
    import inspect

    from openminion.modules.brain.execution.loop_contracts import ExecutionContext

    sig = inspect.signature(ExecutionContext.respond)
    assert "kind" in sig.parameters
    assert sig.parameters["kind"].default == RESPOND_KIND_ASSISTANT


def test_mode_services_respond_with_meta_protocol_accepts_kind() -> None:
    import inspect

    from openminion.modules.brain.execution.ports import ModeServices

    sig = inspect.signature(ModeServices.respond_with_meta)
    assert "kind" in sig.parameters
    assert sig.parameters["kind"].default == RESPOND_KIND_ASSISTANT


def test_mode_services_impl_respond_with_meta_accepts_kind() -> None:
    import inspect

    from openminion.modules.brain.execution.services import RunnerExecutionServices

    sig = inspect.signature(RunnerExecutionServices.respond_with_meta)
    assert "kind" in sig.parameters
    assert sig.parameters["kind"].default == RESPOND_KIND_ASSISTANT


# No prose/keyword heuristic — guard against future drift.


def test_result_from_needs_user_uses_only_structural_state_signal() -> None:
    import ast

    from openminion.modules.brain.loop.adaptive import modes

    source = open(modes.__file__).read()
    tree = ast.parse(source)
    # Locate _result_from_needs_user.
    func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_result_from_needs_user":
            func = node
            break
    assert func is not None, "_result_from_needs_user should exist"

    # Walk the function body and assert no string-constant equals
    # forbidden prose keywords.
    body_nodes = list(func.body)
    forbidden_keywords = {
        "Policy confirmation required",
        "Reply exactly yes",
        "Reply exactly no",
        "exec.run",
        "pytest",
    }
    for sub in ast.walk(ast.Module(body=body_nodes, type_ignores=[])):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            assert sub.value not in forbidden_keywords, (
                f"_result_from_needs_user must not branch on prose keyword "
                f"{sub.value!r}"
            )


# Module-level integration smoke: kind constant + import surfaces.


def test_loop_contracts_kind_field_pinned_at_dataclass() -> None:
    fields = ExecutionResult.__dataclass_fields__
    assert "kind" in fields
    assert fields["kind"].default == RESPOND_KIND_ASSISTANT
