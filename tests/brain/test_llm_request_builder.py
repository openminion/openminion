from __future__ import annotations

from pathlib import Path

from openminion.modules.brain.adapters.llm.request import _build_request


def _patch_tool_bundle(monkeypatch):
    class _Bundle:
        system_tools = []

    monkeypatch.setattr(
        "openminion.modules.brain.adapters.llm.request._TOOL_SCHEMA_SERVICE.get_tools_for_purpose",
        lambda **kwargs: _Bundle(),
    )


def test_build_request_uses_recent_turns_when_pack_messages_have_only_system_and_current_user(
    monkeypatch,
) -> None:
    _patch_tool_bundle(monkeypatch)
    context = {
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "yes, weather"},
        ],
        "turns": [
            {"role": "user", "content": "what's rather at china?"},
            {
                "role": "assistant",
                "content": "Did you mean the weather in China, or something else?",
            },
            {"role": "user", "content": "yes, weather"},
        ],
        "hints": {"user_input": "yes, weather"},
    }

    request = _build_request(
        model="fake-model",
        purpose="decide",
        context=context,
        schema=type("DummySchema", (), {"__name__": "Decision"}),
        temperature=0.0,
    )

    assert [message.role for message in request.messages] == [
        "system",
        "user",
        "assistant",
        "user",
    ]
    assert request.messages[0].content == "sys"
    assert [message.content for message in request.messages[1:]] == [
        "what's rather at china?",
        "Did you mean the weather in China, or something else?",
        "yes, weather",
    ]


def test_build_request_keeps_pack_messages_when_conversation_is_already_present(
    monkeypatch,
) -> None:
    _patch_tool_bundle(monkeypatch)
    context = {
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
            {"role": "user", "content": "weather in tokyo"},
        ],
        "turns": [
            {"role": "user", "content": "ignored old turn"},
            {"role": "assistant", "content": "ignored old reply"},
        ],
        "hints": {"user_input": "weather in tokyo"},
    }

    request = _build_request(
        model="fake-model",
        purpose="decide",
        context=context,
        schema=type("DummySchema", (), {"__name__": "Decision"}),
        temperature=0.0,
    )

    # the `_build_recent_turn_continuity_message` runtime-prose
    # insertion was removed. The test now verifies that the full pack
    # conversation passes through untouched.
    assert [message.role for message in request.messages] == [
        "system",
        "user",
        "assistant",
        "user",
    ]
    assert request.messages[0].content == "sys"
    assert [message.content for message in request.messages[1:]] == [
        "hi",
        "hello",
        "weather in tokyo",
    ]


def test_build_request_inserts_compound_intent_guidance_for_decide_schema(
    monkeypatch,
) -> None:
    _patch_tool_bundle(monkeypatch)
    context = {
        "messages": [
            {"role": "system", "content": "sys"},
            {
                "role": "user",
                "content": "write the script, add tests, and document the API",
            },
        ],
        "hints": {
            "user_input": "write the script, add tests, and document the API",
        },
    }

    request = _build_request(
        model="fake-model",
        purpose="decide",
        context=context,
        schema=type("Decision", (), {}),
        temperature=0.0,
    )

    system_messages = [
        message.content for message in request.messages if message.role == "system"
    ]
    assert any("Decision.sub_intents" in content for content in system_messages)
    assert any(
        "ordered list of short strings" in content for content in system_messages
    )
    assert any(
        "Do not invent extra structure beyond the declared list of strings." in content
        for content in system_messages
    )


def test_build_request_inserts_task_plan_guidance_for_decide_schema(
    monkeypatch,
) -> None:
    _patch_tool_bundle(monkeypatch)
    request = _build_request(
        model="fake-model",
        purpose="decide",
        context={
            "messages": [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "build a multi-step feature"},
            ],
            "hints": {"user_input": "build a multi-step feature"},
        },
        schema=type("Decision", (), {}),
        temperature=0.0,
    )

    system_messages = [
        message.content for message in request.messages if message.role == "system"
    ]
    assert not any("<task_plan>" in content for content in system_messages)
    assert any("plan loop-control tool" in content for content in system_messages)
    assert any("tool_families" in content for content in system_messages)
    assert any("route to act" in content for content in system_messages)
    assert any(
        "Do not emit task-plan XML trailers" in content for content in system_messages
    )
    assert any("Markdown-only plans" in content for content in system_messages)
    assert any(
        "declare, step_completed, step_blocked, revise, abandon, and complete"
        in content
        for content in system_messages
    )


def test_build_request_serializes_structured_context_when_user_input_missing(
    monkeypatch,
) -> None:
    _patch_tool_bundle(monkeypatch)
    request = _build_request(
        model="fake-model",
        purpose="summarize",
        context={
            "subtasks": [
                {"goal": "Fetch uv docs", "status": "done"},
                {"goal": "Fetch pipx docs", "status": "done"},
            ],
            "hints": {
                "instruction": "Synthesize the subtask results into one concise answer."
            },
        },
        schema=type("_SynthesisResponse", (), {"__name__": "_SynthesisResponse"}),
        temperature=0.0,
    )

    assert [message.role for message in request.messages] == ["user"]
    assert "Fetch uv docs" in request.messages[0].content
    assert "Synthesize the subtask results" in request.messages[0].content


def test_build_request_turn_attachments_become_image_content_parts(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _patch_tool_bundle(monkeypatch)
    image_path = tmp_path / "shot.png"
    image_path.write_bytes(b"png")

    request = _build_request(
        model="fake-model",
        purpose="decide",
        context={
            "turns": [
                {
                    "role": "user",
                    "content": "what changed on this screen?",
                    "attachments": [str(image_path)],
                }
            ],
            "hints": {"user_input": "what changed on this screen?"},
        },
        schema=type("Decision", (), {}),
        temperature=0.0,
    )

    assert len(request.messages) >= 1
    user_message = request.messages[-1]
    assert user_message.role == "user"
    assert len(user_message.content_parts) == 2
    assert user_message.content_parts[0].type == "text"
    assert user_message.content_parts[1].type == "image"
    assert user_message.content_parts[1].source == "path"


def test_build_request_preserves_context_message_block_metadata(
    monkeypatch,
) -> None:
    _patch_tool_bundle(monkeypatch)
    request = _build_request(
        model="fake-model",
        purpose="decide",
        context={
            "messages": [
                {
                    "role": "system",
                    "content": "Identity and policy",
                    "meta": {
                        "block_kind": "static_prefix",
                        "cache_eligible": True,
                        "segment_ids": ["static_prefix"],
                        "refs": ["policy:v1"],
                    },
                },
                {
                    "role": "user",
                    "content": "hello",
                },
            ],
            "hints": {"user_input": "hello"},
        },
        schema=type("Decision", (), {}),
        temperature=0.0,
    )

    system_message = request.messages[0]
    assert system_message.role == "system"
    assert len(system_message.content_parts) == 1
    part = system_message.content_parts[0]
    assert part.type == "text"
    assert part.block_kind == "static_prefix"
    assert part.cache_eligible is True
    assert part.segment_ids == ["static_prefix"]
    assert part.refs == ["policy:v1"]


def test_build_request_preserves_budget_telemetry_block_metadata(
    monkeypatch,
) -> None:
    _patch_tool_bundle(monkeypatch)
    request = _build_request(
        model="fake-model",
        purpose="act",
        context={
            "messages": [
                {
                    "role": "system",
                    "content": '[BUDGET TELEMETRY]\n{"iteration_remaining":2}',
                    "meta": {
                        "block_kind": "budget_telemetry",
                        "cache_eligible": False,
                        "segment_ids": ["budget_telemetry"],
                        "refs": [],
                    },
                },
                {"role": "user", "content": "keep going"},
            ],
            "hints": {"user_input": "keep going"},
        },
        schema=type("Decision", (), {}),
        temperature=0.0,
    )

    system_message = request.messages[0]
    part = system_message.content_parts[0]
    assert part.type == "text"
    assert part.block_kind == "budget_telemetry"
    assert part.cache_eligible is False
    assert part.segment_ids == ["budget_telemetry"]


def test_build_request_promotes_structured_timeout_hint_to_request_metadata(
    monkeypatch,
) -> None:
    _patch_tool_bundle(monkeypatch)
    request = _build_request(
        model="fake-model",
        purpose="reflect",
        context={
            "messages": [
                {"role": "user", "content": "extract stable user facts"},
            ],
            "hints": {
                "user_input": "extract stable user facts",
                "structured_timeout_seconds": 20,
            },
        },
        schema=type("UserMessageCandidateReport", (), {}),
        temperature=0.0,
    )

    assert request.metadata["timeout_seconds"] == 20


def test_build_request_preserves_manifest_and_thinking_metadata(
    monkeypatch,
) -> None:
    _patch_tool_bundle(monkeypatch)
    request = _build_request(
        model="fake-model",
        purpose="decide",
        context={
            "messages": [{"role": "user", "content": "hello"}],
            "context_manifest": {
                "prompt_cache_key": "cache-key-1",
                "static_prefix_hash": "prefix-hash-1",
            },
            "hints": {
                "user_input": "hello",
                "thinking_requested_profile": "detailed",
                "thinking_effective_profile": "minimal",
                "thinking_source_layer": "mode_policy",
                "thinking_degraded_reason": "mode_policy_clamp",
                "thinking_degraded_reasons": ["mode_policy_clamp"],
                "thinking_provider_effort": "low",
                "thinking_mode_name": "act",
                "thinking_mode_default_profile": "off",
                "thinking_mode_allowed_profiles": ["off", "minimal"],
                "thinking_mode_request_override_allowed": True,
            },
        },
        schema=type("Decision", (), {}),
        temperature=0.0,
    )

    assert request.metadata["prompt_cache_key"] == "cache-key-1"
    assert request.metadata["static_prefix_hash"] == "prefix-hash-1"
    assert request.metadata["thinking_requested_profile"] == "detailed"
    assert request.metadata["thinking_reasoning_profile"] == "minimal"
    assert request.metadata["thinking_source_layer"] == "mode_policy"
    assert request.metadata["thinking_degraded_reason"] == "mode_policy_clamp"
    assert request.metadata["thinking_degraded_reasons"] == ["mode_policy_clamp"]
    assert request.metadata["thinking"] == "low"
    assert request.metadata["thinking_mode_name"] == "act"
    assert request.metadata["thinking_mode_default_profile"] == "off"
    assert request.metadata["thinking_mode_allowed_profiles"] == ["off", "minimal"]
    assert request.metadata["thinking_mode_request_override_allowed"] is True
