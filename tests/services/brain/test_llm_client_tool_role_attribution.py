from __future__ import annotations

from typing import Any

from openminion.modules.llm.providers.base import ProviderRequest
from openminion.modules.llm.schemas import LLMRequest, Message
from openminion.services.brain.client import OpenMinionLLMClient


class _CapturingProvider:
    def __init__(self) -> None:
        self.name = "capture"
        self.captured: list[ProviderRequest] = []

    async def generate(self, provider_request: ProviderRequest) -> Any:
        self.captured.append(provider_request)
        from types import SimpleNamespace

        return SimpleNamespace(
            text="",
            model="m",
            usage={"prompt_tokens": 0, "completion_tokens": 0},
            tool_calls=[],
            finish_reason="stop",
            normalization={},
            thinking=[],
        )


def _build_client() -> tuple[OpenMinionLLMClient, _CapturingProvider]:
    provider = _CapturingProvider()
    client = OpenMinionLLMClient(provider=provider)
    return client, provider


def _llm_request_with(
    messages: list[Message],
    *,
    metadata: dict[str, str] | None = None,
) -> LLMRequest:
    return LLMRequest(messages=messages, tools=None, metadata=dict(metadata or {}))


def test_tool_role_message_survives_brain_llm_client_normalization() -> None:
    client, provider = _build_client()
    tool_envelope = (
        '{"status": "success", "summary": "file listing ok", '
        '"outputs": {"path": "/tmp"}}'
    )
    req = _llm_request_with(
        messages=[
            Message(role="system", content="Native tool-calling contract..."),
            Message(role="user", content="list /tmp"),
            Message(role="assistant", content="Calling file.list_dir."),
            Message(
                role="tool",
                content=tool_envelope,
                meta={"tool_call_id": "call-1", "tool_name": "file.list_dir"},
            ),
        ]
    )

    client.call(req)

    assert provider.captured, "BrainLLMClient should have invoked the provider"
    pr = provider.captured[-1]
    # The tool envelope must remain in history with role="tool", and
    # must NOT have been promoted into user_message.
    tool_history_items = [
        item for item in pr.history if str(item.role).lower() == "tool"
    ]
    assert len(tool_history_items) == 1, (
        "PTFI-03 v2: a Message(role='tool') in LLMRequest.messages must "
        "round-trip into ProviderRequest.history with role='tool' "
        "(observed history roles: "
        f"{[item.role for item in pr.history]})"
    )
    assert tool_history_items[0].content == tool_envelope, (
        "PTFI-03 v2: tool envelope content must round-trip unchanged"
    )
    assert tool_history_items[0].meta == {
        "tool_call_id": "call-1",
        "tool_name": "file.list_dir",
    }, "Tool-call replay metadata must survive into ProviderRequest.history"
    # And the tool envelope must NEVER appear in user_message — that
    # was the live-trace symptom.
    assert pr.user_message != tool_envelope, (
        "PTFI-03 v2: tool envelope must not be promoted into "
        "ProviderRequest.user_message — that re-attributes the tool "
        "result as a fresh user turn at the provider boundary"
    )


def test_user_role_message_still_becomes_user_message() -> None:
    client, provider = _build_client()
    req = _llm_request_with(
        messages=[
            Message(role="system", content="sys"),
            Message(role="user", content="what is 2 + 2"),
        ]
    )

    client.call(req)
    pr = provider.captured[-1]
    assert pr.user_message == "what is 2 + 2", (
        "Legacy contract: the trailing user message remains the prompt"
    )
    assert pr.history == [], (
        "Legacy contract: a single user message produces empty history"
    )


def test_tool_message_followed_by_user_message_keeps_user_as_prompt() -> None:
    client, provider = _build_client()
    tool_envelope = (
        '{"status": "success", "summary": "computed ok", "outputs": {"answer": 42}}'
    )
    req = _llm_request_with(
        messages=[
            Message(role="system", content="sys"),
            Message(role="user", content="compute answer"),
            Message(role="assistant", content="Calling compute."),
            Message(role="tool", content=tool_envelope),
            Message(role="user", content="also explain it"),
        ]
    )

    client.call(req)
    pr = provider.captured[-1]
    assert pr.user_message == "also explain it", (
        "PTFI-03 v2: the trailing user follow-up should be the prompt"
    )
    history_roles = [str(item.role).lower() for item in pr.history]
    # The history should contain: the original user query, the
    # assistant turn, and the tool envelope — all with their roles
    # preserved.
    assert "tool" in history_roles, (
        f"PTFI-03 v2: tool envelope must remain in history "
        f"(observed roles: {history_roles})"
    )
    assert history_roles.count("tool") == 1, (
        "PTFI-03 v2: exactly one tool envelope expected in history"
    )
    # And the tool envelope content survives unchanged.
    tool_items = [item for item in pr.history if item.role == "tool"]
    assert tool_items[0].content == tool_envelope


def test_tool_message_as_only_trailing_history_routes_to_history_not_prompt() -> None:
    client, provider = _build_client()
    tool_envelope = (
        '{"status": "success", "summary": "{\\"count\\": 8}", "outputs": {"ok": true}}'
    )
    req = _llm_request_with(
        messages=[
            Message(role="system", content="sys"),
            Message(role="user", content="explore the project"),
            Message(role="assistant", content="Calling file.list_dir."),
            Message(role="tool", content=tool_envelope),
        ]
    )

    client.call(req)
    pr = provider.captured[-1]
    # The most recent user-role message becomes the prompt.
    assert pr.user_message == "explore the project", (
        "PTFI-03 v2: when the trailing message is role='tool', the "
        "BrainLLMClient must walk back to the most recent user-role "
        "message for `user_message`, never promote the tool envelope"
    )
    # The tool envelope sits in history with role='tool'.
    tool_items = [item for item in pr.history if str(item.role).lower() == "tool"]
    assert len(tool_items) == 1
    assert tool_items[0].content == tool_envelope
    # The tool envelope MUST NOT appear in any user-role history item.
    user_tool_items = [
        item
        for item in pr.history
        if str(item.role).lower() == "user" and item.content == tool_envelope
    ]
    assert user_tool_items == [], (
        "PTFI-03 v2: tool envelope content must not appear in any "
        "user-role history item"
    )


def test_tool_message_without_user_gets_continuation_prompt_not_tool_payload() -> None:
    client, provider = _build_client()
    tool_envelope = (
        '{"status": "success", "summary": "file listing ok", '
        '"outputs": {"entries": ["pyproject.toml"]}}'
    )
    req = _llm_request_with(
        messages=[
            Message(
                role="tool",
                content=tool_envelope,
                meta={"tool_call_id": "call-1", "tool_name": "file.list_dir"},
            ),
        ]
    )

    client.call(req)
    pr = provider.captured[-1]

    assert "Continue the active task" in pr.user_message
    assert "Do not restart completed steps" not in pr.user_message
    assert pr.user_message != tool_envelope
    tool_items = [item for item in pr.history if str(item.role).lower() == "tool"]
    assert len(tool_items) == 1
    assert tool_items[0].content == tool_envelope


def test_tool_message_without_user_prefers_original_request_metadata() -> None:
    client, provider = _build_client()
    tool_envelope = (
        '{"status": "success", "summary": "file listing ok", '
        '"outputs": {"entries": ["pyproject.toml"]}}'
    )
    req = _llm_request_with(
        messages=[
            Message(
                role="tool",
                content=tool_envelope,
                meta={"tool_call_id": "call-1", "tool_name": "file.list_dir"},
            ),
        ],
        metadata={
            "user_input": "Rewrite pyproject.toml and README.md, then run pytest.",
            "purpose": "act",
        },
    )

    client.call(req)
    pr = provider.captured[-1]

    assert "Continue the active task" in pr.user_message
    assert "Do not restart completed steps" in pr.user_message
    assert "Rewrite pyproject.toml and README.md, then run pytest." in pr.user_message
    assert (
        "Successful tool calls already completed in this turn: file.list_dir."
        in pr.user_message
    )
    assert pr.user_message != tool_envelope
    tool_items = [item for item in pr.history if str(item.role).lower() == "tool"]
    assert len(tool_items) == 1
    assert tool_items[0].content == tool_envelope
