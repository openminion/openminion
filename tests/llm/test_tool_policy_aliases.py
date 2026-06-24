import pytest
from openminion.modules.llm import LLMCTL
from openminion.modules.llm.runtime.client import ToolPolicyContext
from openminion.modules.llm.schemas import LLMResponse, ToolCall, UsageInfo


@pytest.fixture
def client():
    llmctl = LLMCTL.from_config(
        {
            "version": 1,
            "llmctl": {"default_provider": "stub", "default_model": "stub-v1"},
            "providers": {},
            "agents": {},
        }
    )
    return llmctl.client()


def _policy_context() -> ToolPolicyContext:
    return ToolPolicyContext(
        enabled=True,
        allowed_tools={"browser.playwright.navigate"},
        block_on_disallowed_tool_call=True,
    )


def _tool_response(name: str, arguments: dict[str, object]) -> LLMResponse:
    return LLMResponse(
        ok=True,
        provider="stub",
        model="stub-v1",
        output_text="",
        tool_calls=[ToolCall(name=name, arguments=arguments, status="requested")],
        usage=UsageInfo(),
        latency_ms=1,
    )


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("browser.run", {"url": "https://www.google.com"}),
        ("totally.unknown.tool", {}),
    ],
)
def test_policy_post_blocks_disallowed_or_unresolvable_tools(
    client,
    name: str,
    arguments: dict[str, object],
) -> None:
    response = LLMResponse(**_tool_response(name, arguments).model_dump())

    out = client._apply_tool_policy_post(response, _policy_context())
    assert out.ok is False
    assert out.error is not None
    assert out.error.code == "POLICY_DENIED"
    assert len(out.tool_calls) == 1
    assert out.tool_calls[0].name == name
    assert out.tool_calls[0].status == "blocked"
