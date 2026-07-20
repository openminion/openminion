from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from openminion.modules.brain.loop.tools.shortlisting import (
    TOOL_REQUEST_TOOL_NAME,
    TOOL_SCHEMA_SHORTLIST_THRESHOLD,
    build_inactive_tool_directory_message,
    build_tool_request_spec,
    shortlist_tool_schemas,
    should_shortlist_tool_schemas,
    with_tool_request_spec,
)
from openminion.modules.llm.schemas import LLMResponse, Message, ToolSpec, UsageInfo


@dataclass
class _ShortlistRuntime:
    response: LLMResponse
    calls: list[dict[str, Any]] = field(default_factory=list)

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
                "messages": list(messages),
                "tools": list(tools),
                "model": model,
                "tool_choice": tool_choice,
                "max_output_tokens": max_output_tokens,
                "metadata": metadata,
            }
        )
        return self.response


def _specs(*names: str) -> list[ToolSpec]:
    return [
        ToolSpec(
            name=name,
            description=f"{name} description",
            input_schema={"type": "object", "properties": {}},
        )
        for name in names
    ]


def test_shortlist_tool_schemas_uses_model_authored_exact_names() -> None:
    runtime = _ShortlistRuntime(
        response=LLMResponse(
            ok=True,
            provider="fake",
            model="fake",
            output_text='{"tool_ids":["web.search","not.real","web.fetch","web.search"]}',
            usage=UsageInfo(input_tokens=100, output_tokens=12),
        )
    )
    tool_specs = _specs(
        "file.read",
        "file.find",
        "file.search",
        "exec.run",
        "web.search",
        "web.fetch",
        "weather",
        "time",
        "location",
    )

    result = shortlist_tool_schemas(
        runtime=runtime,
        model="fake-model",
        user_messages=[Message(role="user", content="latest news")],
        tool_specs=tool_specs,
        metadata={"purpose": "tool_schema_shortlist"},
    )

    assert result.enabled is True
    assert result.selected_tool_names == ("web.search", "web.fetch")
    assert [spec.name for spec in result.active_tool_specs] == [
        "web.search",
        "web.fetch",
    ]
    assert "not.real" not in result.selected_tool_names
    assert result.total_tokens == 112
    assert result.llm_call_made is True
    assert runtime.calls[0]["tools"] == []
    assert runtime.calls[0]["tool_choice"] == "none"
    assert runtime.calls[0]["metadata"] == {"purpose": "tool_schema_shortlist"}


def test_shortlist_tool_schemas_skips_small_tool_surfaces() -> None:
    runtime = _ShortlistRuntime(
        response=LLMResponse(ok=True, provider="fake", model="fake", output_text="")
    )

    result = shortlist_tool_schemas(
        runtime=runtime,
        model="fake-model",
        user_messages=[Message(role="user", content="read file")],
        tool_specs=_specs(
            *[f"tool.{idx}" for idx in range(TOOL_SCHEMA_SHORTLIST_THRESHOLD)]
        ),
    )

    assert result.enabled is False
    assert result.reason == "below_threshold"
    assert result.llm_call_made is False
    assert runtime.calls == []


def test_tool_request_spec_and_directory_are_loop_control_only() -> None:
    active_specs = _specs("web.search")
    requestable_specs = _specs("web.search", "web.fetch", "time")
    specs_with_request = with_tool_request_spec(active_specs)
    directory = build_inactive_tool_directory_message(
        requestable_tool_specs=requestable_specs,
        active_tool_names={"web.search"},
    )

    assert should_shortlist_tool_schemas(
        profile_name="general_adaptive_v1",
        tool_specs=_specs(*[f"tool.{idx}" for idx in range(9)]),
    )
    assert not should_shortlist_tool_schemas(
        profile_name="watch_check_v1",
        tool_specs=_specs(*[f"tool.{idx}" for idx in range(9)]),
    )
    request_spec = build_tool_request_spec()
    assert request_spec.name == TOOL_REQUEST_TOOL_NAME
    assert request_spec.input_schema["required"] == [
        "name",
        "terminal_after_success",
    ]
    assert [spec.name for spec in specs_with_request] == [
        "web.search",
        TOOL_REQUEST_TOOL_NAME,
    ]
    assert directory is not None
    assert "web.fetch" in directory.content
    assert "web.search:" not in directory.content
