from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from openminion.modules.brain.loop.strategies.coding.contracts import (
    CODING_ALLOWED_TOOLS,
    CODING_TERM_APPROVAL_NEEDED,
    CODING_TERM_BUDGET_EXHAUSTED,
    CODING_TERM_DISALLOWED_TOOL,
    CODING_TERM_FINAL_TEXT,
    CODING_TERM_ITERATION_CAP,
    CODING_TERM_JOB_PENDING,
    CODING_TERM_LLM_ERROR,
    CODING_TERM_NEEDS_USER,
    CODING_TERM_TOOL_FAILURE,
    CODING_V1_ALLOWED_TOOLS,
    CodingDisallowedToolError,
    CodingLLMRuntime,
    CodingModeError,
    CodingRuntimeUnavailableError,
)
from openminion.modules.brain.loop.strategies.coding.loop_state import (
    CodingLoopState,
)
from openminion.modules.tool.runtime.policy import DEFAULT_POLICY
from openminion.modules.brain.loop.strategies.coding.llm import (
    DefaultCodingLLMRuntime,
    _unwrap_llm_client,
)
from openminion.modules.brain.loop.strategies.coding.tool_messages import (
    action_result_to_tool_message,
    format_blocking_tool_message,
)
from openminion.modules.brain.schemas import ActionError, ActionResult, new_uuid
from openminion.modules.llm.schemas import LLMResponse, Message, ToolSpec


def test_v1_allowlist_contains_expected_tools() -> None:
    expected = {
        "code.grep",
        "code.patch",
        "code.repo_index",
        "code.repo_map",
        "code.symbol_find",
        "file.list_dir",
        "file.read",
        "file.read_range",
        "file.find",
        "file.write",
        "web.fetch",
        "exec.run",
        "exec.poll",
        "exec.list",
        "exec.kill",
    }
    assert expected == CODING_ALLOWED_TOOLS
    assert CODING_V1_ALLOWED_TOOLS == CODING_ALLOWED_TOOLS


def test_v1_allowlist_excludes_pty_tools() -> None:
    excluded = {"exec.send_keys", "exec.paste", "exec.submit", "exec.clear"}
    for tool in excluded:
        assert tool not in CODING_ALLOWED_TOOLS


def test_v1_allowlist_excludes_interactive_or_search_web_tools() -> None:
    assert "web.fetch" in CODING_ALLOWED_TOOLS
    for tool in ("web.search", "browser"):
        assert tool not in CODING_ALLOWED_TOOLS


def test_default_tool_policy_allows_code_prefix_for_coding_tools() -> None:
    allow_prefix = list((DEFAULT_POLICY.get("tools", {}) or {}).get("allow_prefix", []))
    assert "code." in allow_prefix


def test_default_tool_policy_allows_tool_catalog_prefix() -> None:
    allow_prefix = list((DEFAULT_POLICY.get("tools", {}) or {}).get("allow_prefix", []))
    assert "tool." in allow_prefix


def test_termination_reason_constants() -> None:
    assert CODING_TERM_FINAL_TEXT == "final_text"
    assert CODING_TERM_APPROVAL_NEEDED == "approval_needed"
    assert CODING_TERM_NEEDS_USER == "needs_user"
    assert CODING_TERM_JOB_PENDING == "job_pending"
    assert CODING_TERM_BUDGET_EXHAUSTED == "budget_exhausted"
    assert CODING_TERM_DISALLOWED_TOOL == "disallowed_tool"
    assert CODING_TERM_LLM_ERROR == "llm_error"
    assert CODING_TERM_TOOL_FAILURE == "tool_failure"
    assert CODING_TERM_ITERATION_CAP == "iteration_cap"


class _FakeRuntime:
    def complete(
        self,
        *,
        messages: list[Message],
        tools: list[ToolSpec],
        model: str,
        tool_choice: Any = "auto",
        max_output_tokens: Any = None,
        metadata: Any = None,
    ) -> LLMResponse:
        return LLMResponse(ok=True, provider="test", model=model, output_text="ok")


def test_coding_llm_runtime_is_runtime_checkable() -> None:
    runtime = _FakeRuntime()
    assert isinstance(runtime, CodingLLMRuntime)


def test_non_runtime_is_not_coding_llm_runtime() -> None:
    class _NotRuntime:
        pass

    assert not isinstance(_NotRuntime(), CodingLLMRuntime)


# Error types


def test_coding_mode_error_hierarchy() -> None:
    assert issubclass(CodingRuntimeUnavailableError, CodingModeError)
    assert issubclass(CodingDisallowedToolError, CodingModeError)


def test_coding_disallowed_tool_error_captures_name() -> None:
    err = CodingDisallowedToolError("browser")
    assert err.tool_name == "browser"
    assert "browser" in str(err)


# CodingLoopState


def test_loop_state_initial_values() -> None:
    state = CodingLoopState()
    assert state.iteration == 0
    assert state.llm_calls == 0
    assert state.tool_calls_made == []
    assert state.termination_reason == ""
    assert state.messages == []
    assert state.scratchpad == {}


def test_loop_state_telemetry_payload_structure() -> None:
    state = CodingLoopState(
        iteration=3,
        llm_calls=3,
        tool_calls_made=["file.read", "exec.run"],
        termination_reason="final_text",
    )
    payload = state.telemetry_payload(CODING_ALLOWED_TOOLS)
    assert payload["coding.loop_iterations"] == 3
    assert payload["coding.llm_calls"] == 3
    assert set(payload["coding.tool_calls"]) == {"file.read", "exec.run"}
    assert payload["coding.termination_reason"] == "final_text"
    assert set(payload["coding.allowed_tools"]) == set(CODING_ALLOWED_TOOLS)


# DefaultCodingLLMRuntime — adapter unwrapping


def _make_llm_client() -> Any:
    client = MagicMock()
    client.complete = MagicMock(
        return_value=LLMResponse(
            ok=True, provider="test", model="test-model", output_text="hello"
        )
    )
    return client


def test_unwrap_via_dot_client() -> None:
    llm_client = _make_llm_client()
    adapter = SimpleNamespace(client=llm_client)
    unwrapped = _unwrap_llm_client(adapter)
    assert unwrapped is llm_client


def test_unwrap_via_dot_llm() -> None:
    llm_client = _make_llm_client()
    adapter = SimpleNamespace(llm=llm_client)
    unwrapped = _unwrap_llm_client(adapter)
    assert unwrapped is llm_client


def test_unwrap_when_adapter_is_client() -> None:
    # Use a SimpleNamespace with a complete() callable rather than MagicMock
    # (MagicMock auto-creates .client on access, breaking the identity check)
    raw = SimpleNamespace(complete=lambda *a, **kw: None)
    unwrapped = _unwrap_llm_client(raw)
    assert unwrapped is raw


def test_unwrap_returns_none_when_unavailable() -> None:
    adapter = SimpleNamespace(foo="bar")
    assert _unwrap_llm_client(adapter) is None


def test_from_adapter_raises_when_unavailable() -> None:
    adapter = SimpleNamespace()
    with pytest.raises(CodingRuntimeUnavailableError):
        DefaultCodingLLMRuntime.from_adapter(adapter)


def test_from_adapter_succeeds_with_client() -> None:
    llm_client = _make_llm_client()
    adapter = SimpleNamespace(client=llm_client)
    runtime = DefaultCodingLLMRuntime.from_adapter(adapter)
    assert isinstance(runtime, DefaultCodingLLMRuntime)
    assert isinstance(runtime, CodingLLMRuntime)


def test_runtime_complete_translates_to_overrides() -> None:
    llm_client = _make_llm_client()
    adapter = SimpleNamespace(client=llm_client)
    runtime = DefaultCodingLLMRuntime.from_adapter(adapter)

    msgs = [Message(role="user", content="hello")]
    tools = [ToolSpec(name="file.read", description="read")]
    response = runtime.complete(
        messages=msgs, tools=tools, model="test-model", tool_choice="auto"
    )
    assert response.ok
    llm_client.complete.assert_called_once()
    call_args = llm_client.complete.call_args
    # First positional arg is messages
    assert call_args[0][0] == msgs
    # Second positional arg is tools
    assert call_args[0][1] == tools
    # model passed via **overrides
    assert call_args[1]["model"] == "test-model"


def test_runtime_complete_passes_none_tools_when_empty() -> None:
    llm_client = _make_llm_client()
    adapter = SimpleNamespace(client=llm_client)
    runtime = DefaultCodingLLMRuntime.from_adapter(adapter)
    runtime.complete(messages=[], tools=[], model="m")
    call_args = llm_client.complete.call_args
    # tools=[] should become None
    assert call_args[0][1] is None


# tool_messages helpers


def test_action_result_to_tool_message_success() -> None:
    result = ActionResult(
        command_id=new_uuid(),
        status="success",
        summary="read ok",
        outputs={"content": "hello world"},
    )
    msg = action_result_to_tool_message(
        tool_call_id="tc-1", tool_name="file.read", action_result=result
    )
    assert msg.role == "tool"
    import json

    payload = json.loads(msg.content)
    assert payload["status"] == "success"
    assert payload["outputs"]["content"] == "hello world"
    assert msg.meta["tool_call_id"] == "tc-1"
    assert msg.meta["tool_name"] == "file.read"


def test_action_result_to_tool_message_error() -> None:
    result = ActionResult(
        command_id=new_uuid(),
        status="failed",
        summary="file not found",
        error=ActionError(code="FILE_NOT_FOUND", message="file not found"),
    )
    msg = action_result_to_tool_message(
        tool_call_id=None, tool_name="file.read", action_result=result
    )
    import json

    payload = json.loads(msg.content)
    assert payload["status"] == "failed"
    assert payload["error"]["code"] == "FILE_NOT_FOUND"
    assert "tool_call_id" not in msg.meta


def test_format_blocking_tool_message() -> None:
    msg = format_blocking_tool_message(
        tool_name="browser",
        reason="not allowed",
        termination_reason="disallowed_tool",
    )
    assert msg.role == "tool"
    import json

    payload = json.loads(msg.content)
    assert payload["status"] == "blocked"
    assert payload["termination_reason"] == "disallowed_tool"
    assert msg.meta["tool_name"] == "browser"
