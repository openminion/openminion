from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from unittest import mock

from openminion.modules.brain.loop.entry import build_entry_tool_specs
from openminion.modules.brain.loop.tools.contracts import AdaptiveToolLoopState
from openminion.modules.brain.loop.tools.iteration.dispatch import (
    _process_review_tool_calls,
)
from openminion.modules.brain.loop.tools.review_control import (
    REVIEW_TOOL_ATTEMPTED_SCRATCHPAD_KEY,
    REVIEW_TOOL_NAME,
    REVIEW_TOOL_USED_SCRATCHPAD_KEY,
)
from openminion.modules.brain.runtime.review.observation import (
    observe_review_invocation,
)


@dataclass
class _FakeToolCall:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    id: str = "tc-1"


def _fake_profile():
    return SimpleNamespace(
        profile_name="test-profile",
        mode_name="act",
        max_iterations=1,
    )


def _fake_loop_ctx():
    return SimpleNamespace(
        state=SimpleNamespace(
            budgets_remaining=SimpleNamespace(tokens=1000, tool_calls=10),
            llm_calls_used=0,
            llm_calls_max=10,
            trace_id="trace-x",
        ),
        emit_status=mock.MagicMock(),
    )


def _run_dispatcher(
    *,
    tool_calls: list[_FakeToolCall],
    loop_state: AdaptiveToolLoopState | None = None,
) -> tuple[list[Any], bool, Any, AdaptiveToolLoopState, mock.MagicMock]:
    if loop_state is None:
        loop_state = AdaptiveToolLoopState()
    append_callback = mock.MagicMock()
    set_turn_progress = mock.MagicMock()
    on_tool_result = mock.MagicMock()
    iter_tool_records: list[Any] = []
    with (
        mock.patch(
            "openminion.modules.brain.loop.tools.iteration.dispatch._emit_iteration_event"
        ),
        mock.patch(
            "openminion.modules.brain.loop.tools.iteration.dispatch.emit_adaptive_status"
        ),
    ):
        remaining, progress, result = _process_review_tool_calls(
            _fake_loop_ctx(),
            profile=_fake_profile(),
            loop_state=loop_state,
            tool_calls=list(tool_calls),
            public_mode_tag="act",
            signature="sig-x",
            iter_tool_records=iter_tool_records,
            iter_llm_duration_ms=10,
            iter_input_tokens=5,
            iter_output_tokens=5,
            on_tool_result=on_tool_result,
            append_tool_result_payload=append_callback,
            set_turn_progress=set_turn_progress,
        )
    return remaining, progress, result, loop_state, append_callback, iter_tool_records


def test_no_review_calls_passes_through_unchanged() -> None:
    calls = [
        _FakeToolCall(name="exec.run", arguments={"argv": ["ls"]}),
        _FakeToolCall(name="file.read", arguments={"path": "foo.py"}),
    ]
    remaining, progress, result, loop_state, append_cb, iter_records = _run_dispatcher(
        tool_calls=calls
    )
    assert remaining == calls
    assert progress is False
    assert result is None
    assert REVIEW_TOOL_ATTEMPTED_SCRATCHPAD_KEY not in loop_state.scratchpad
    assert REVIEW_TOOL_USED_SCRATCHPAD_KEY not in loop_state.scratchpad
    append_cb.assert_not_called()
    assert iter_records == []


def test_review_only_batch_returns_dispatch_result() -> None:
    diff = (
        "diff --git a/tests/test_a.py b/tests/test_a.py\n"
        "--- a/tests/test_a.py\n+++ b/tests/test_a.py\n"
        "@@ -1 +1 @@\n-old\n+new\n"
    )
    calls = [_FakeToolCall(name=REVIEW_TOOL_NAME, arguments={"diff": diff})]
    remaining, progress, result, loop_state, append_cb, iter_records = _run_dispatcher(
        tool_calls=calls
    )
    assert result is not None
    assert progress is True
    assert loop_state.scratchpad[REVIEW_TOOL_ATTEMPTED_SCRATCHPAD_KEY] is True
    assert loop_state.scratchpad[REVIEW_TOOL_USED_SCRATCHPAD_KEY] is True
    assert append_cb.call_count == 1
    kwargs = append_cb.call_args.kwargs
    assert kwargs["tool_name"] == REVIEW_TOOL_NAME
    assert len(iter_records) == 1
    record = iter_records[0]
    assert record.tool_name == REVIEW_TOOL_NAME


def test_mixed_batch_routes_review_and_returns_regular_calls() -> None:
    diff = (
        "diff --git a/tests/test_a.py b/tests/test_a.py\n"
        "--- a/tests/test_a.py\n+++ b/tests/test_a.py\n"
        "@@ -1 +1 @@\n-old\n+new\n"
    )
    calls = [
        _FakeToolCall(name="exec.run", arguments={"argv": ["ls"]}),
        _FakeToolCall(name=REVIEW_TOOL_NAME, arguments={"diff": diff}),
        _FakeToolCall(name="file.read", arguments={"path": "foo.py"}),
    ]
    remaining, progress, result, loop_state, append_cb, iter_records = _run_dispatcher(
        tool_calls=calls
    )
    assert result is None
    assert progress is True
    assert len(remaining) == 2
    assert [c.name for c in remaining] == ["exec.run", "file.read"]
    assert loop_state.scratchpad[REVIEW_TOOL_USED_SCRATCHPAD_KEY] is True
    assert append_cb.call_count == 1


def test_handler_failure_records_attempt_not_used() -> None:
    calls = [_FakeToolCall(name=REVIEW_TOOL_NAME, arguments={"diff": ""})]
    _, _, result, loop_state, append_cb, iter_records = _run_dispatcher(
        tool_calls=calls
    )
    assert loop_state.scratchpad[REVIEW_TOOL_ATTEMPTED_SCRATCHPAD_KEY] is True
    assert loop_state.scratchpad.get(REVIEW_TOOL_USED_SCRATCHPAD_KEY) is None
    assert append_cb.call_count == 1
    assert iter_records[0].tool_name == REVIEW_TOOL_NAME
    assert iter_records[0].status != "success"


def test_multiple_review_calls_all_dispatched() -> None:
    diff = (
        "diff --git a/tests/test_a.py b/tests/test_a.py\n"
        "--- a/tests/test_a.py\n+++ b/tests/test_a.py\n"
        "@@ -1 +1 @@\n-old\n+new\n"
    )
    calls = [
        _FakeToolCall(name=REVIEW_TOOL_NAME, arguments={"diff": diff}, id="tc-1"),
        _FakeToolCall(name=REVIEW_TOOL_NAME, arguments={"diff": diff}, id="tc-2"),
    ]
    _, _, result, loop_state, append_cb, iter_records = _run_dispatcher(
        tool_calls=calls
    )
    assert result is not None  # batch was review-only
    assert append_cb.call_count == 2
    assert len(iter_records) == 2


def test_build_entry_tool_specs_registers_review_diff() -> None:
    specs, _supports_seed = build_entry_tool_specs(
        runner=None,
        act_profile="general",
        execution_target_kind="local",
        include_control_tools=True,
    )
    spec_names = [getattr(s, "name", "") for s in specs]
    assert REVIEW_TOOL_NAME in spec_names


def test_build_entry_tool_specs_does_not_register_when_control_tools_disabled() -> None:
    specs, _supports_seed = build_entry_tool_specs(
        runner=None,
        act_profile="general",
        execution_target_kind="local",
        include_control_tools=False,
    )
    spec_names = [getattr(s, "name", "") for s in specs]
    assert REVIEW_TOOL_NAME not in spec_names


def test_dispatcher_output_shape_is_observable_by_review_observer() -> None:
    from openminion.modules.brain.loop.tools.review_control import (
        handle_review_tool_call,
    )
    from openminion.modules.brain.loop.tools.iteration.helpers import (
        _tool_result_payload_from_action,
    )

    diff = (
        "diff --git a/src/foo.py b/src/foo.py\n"
        "--- a/src/foo.py\n+++ b/src/foo.py\n"
        "@@ -1 +1 @@\n-old\n+new\n"
    )
    action_result = handle_review_tool_call(loop_ctx=None, arguments={"diff": diff})
    payload_entry = _tool_result_payload_from_action(
        tool_name=REVIEW_TOOL_NAME, action_result=action_result
    )
    fact = observe_review_invocation([payload_entry])
    assert fact.invoked is True
    assert fact.severity in {"ok", "warn", "block"}
    assert isinstance(fact.findings_count, int)
    assert fact.findings_count >= 0


def test_dispatcher_routing_does_not_pollute_other_dispatchers() -> None:
    # Multi-tool batch with one review and one runtime tool; the
    # runtime tool should pass through unchanged for the next
    # dispatcher in the chain.
    diff = (
        "diff --git a/tests/test_a.py b/tests/test_a.py\n"
        "--- a/tests/test_a.py\n+++ b/tests/test_a.py\n"
        "@@ -1 +1 @@\n-old\n+new\n"
    )
    runtime_call = _FakeToolCall(name="exec.run", arguments={"argv": ["pytest"]})
    review_call = _FakeToolCall(name=REVIEW_TOOL_NAME, arguments={"diff": diff})
    remaining, _, result, _, _, _ = _run_dispatcher(
        tool_calls=[runtime_call, review_call]
    )
    assert result is None
    assert remaining == [runtime_call]
