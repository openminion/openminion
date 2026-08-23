from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from openminion.modules.artifact.errors import ArtifactCtlError
from openminion.modules.brain.adapters.llm.request import _build_request
from openminion.modules.brain.schemas import Decision, UserMessageCandidateReport
from openminion.modules.llm.errors import LLMCtlError


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


def test_build_request_excludes_attachment_images_from_user_message_candidate_report(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _patch_tool_bundle(monkeypatch)
    artifact_ref = "artifact://sha256/" + "a" * 64
    image_path = tmp_path / "shot.png"
    image_path.write_bytes(b"png")
    inspected: list[str] = []
    monkeypatch.setattr(
        "openminion.modules.brain.adapters.llm.request.inspect_artifact_image",
        lambda ref: (inspected.append(ref) or "image/png", 1),
    )

    def _context() -> dict[str, object]:
        return {
            "messages": [
                {"role": "assistant", "content": "Ready."},
                {
                    "role": "user",
                    "content": "What changed?",
                    "content_parts": [
                        {
                            "type": "text",
                            "text": "What changed?",
                            "segment_ids": ["turn:current"],
                        },
                        {
                            "type": "image",
                            "source": "url",
                            "url": "https://example.invalid/normalized.png",
                            "mime_type": "image/png",
                        },
                    ],
                },
            ],
            "turns": [
                {
                    "turn_id": "current",
                    "role": "user",
                    "content": "What changed?",
                    "attachments": [artifact_ref, str(image_path)],
                }
            ],
            "hints": {"user_input": "What changed?"},
        }

    auxiliary = _build_request(
        model="fake-model",
        purpose="reflect",
        context=_context(),
        schema=UserMessageCandidateReport,
        temperature=0.0,
    )
    assert inspected == []
    assert any(message.content == "What changed?" for message in auxiliary.messages)
    assert [
        part
        for message in auxiliary.messages
        for part in message.content_parts
        if part.type == "image"
    ] == []

    same_name_impostor = type("UserMessageCandidateReport", (), {})
    ordinary = _build_request(
        model="fake-model",
        purpose="decide",
        context=_context(),
        schema=Decision,
        temperature=0.0,
    )
    impostor = _build_request(
        model="fake-model",
        purpose="reflect",
        context=_context(),
        schema=same_name_impostor,
        temperature=0.0,
    )

    assert inspected == [artifact_ref, artifact_ref]
    for request in (ordinary, impostor):
        image_sources = [
            part.source
            for message in request.messages
            for part in message.content_parts
            if part.type == "image"
        ]
        assert image_sources == ["url", "path", "artifact"]


def test_build_request_selects_current_then_newest_historical_artifacts(
    monkeypatch,
) -> None:
    _patch_tool_bundle(monkeypatch)
    refs = [f"artifact://sha256/{value * 64}" for value in "abcde"]
    sizes = {
        refs[0]: 8 * 1024 * 1024,
        refs[1]: 6 * 1024 * 1024,
        refs[2]: 6 * 1024 * 1024,
        refs[3]: 6 * 1024 * 1024,
        refs[4]: 1 * 1024 * 1024,
    }
    monkeypatch.setattr(
        "openminion.modules.brain.adapters.llm.request.inspect_artifact_image",
        lambda ref: ("image/png", sizes[ref]),
    )
    turns = [
        {"role": "user", "content": "oldest", "attachments": [refs[0]]},
        {"role": "assistant", "content": "old reply"},
        {"role": "user", "content": "newer", "attachments": [refs[1], refs[2]]},
        {"role": "assistant", "content": "newer reply"},
        {"role": "user", "content": "current", "attachments": [refs[4]]},
    ]
    request = _build_request(
        model="fake-model",
        purpose="decide",
        context={
            "messages": [
                {"role": "system", "content": "sys"},
                *[{"role": turn["role"], "content": turn["content"]} for turn in turns],
            ],
            "turns": turns,
            "hints": {"user_input": "current"},
        },
        schema=type("Decision", (), {}),
        temperature=0.0,
    )

    selected = {
        message.content: [
            part.artifact_ref
            for part in message.content_parts
            if getattr(part, "source", "") == "artifact"
        ]
        for message in request.messages
        if message.role == "user"
    }
    assert selected["current"] == [refs[4]]
    assert selected["newer"] == [refs[1], refs[2]]
    assert selected["oldest"] == []


def test_build_request_rejects_unavailable_artifact_before_provider(
    monkeypatch,
) -> None:
    _patch_tool_bundle(monkeypatch)
    ref = f"artifact://sha256/{'f' * 64}"

    def _missing(_ref: str):
        raise ArtifactCtlError("NOT_FOUND", "Artifact image is unavailable")

    monkeypatch.setattr(
        "openminion.modules.brain.adapters.llm.request.inspect_artifact_image",
        _missing,
    )
    with pytest.raises(LLMCtlError, match="Artifact image is unavailable"):
        _build_request(
            model="fake-model",
            purpose="decide",
            context={
                "turns": [{"role": "user", "content": "inspect", "attachments": [ref]}],
                "hints": {"user_input": "inspect"},
            },
            schema=type("Decision", (), {}),
            temperature=0.0,
        )


def test_build_request_aligns_images_to_compacted_budgeted_turns(monkeypatch) -> None:
    _patch_tool_bundle(monkeypatch)
    current_ref = "artifact://sha256/" + "c" * 64
    trimmed_ref = "artifact://sha256/" + "d" * 64
    inspected: list[str] = []

    def _inspect(ref: str):
        inspected.append(ref)
        return "image/png", 1

    monkeypatch.setattr(
        "openminion.modules.brain.adapters.llm.request.inspect_artifact_image",
        _inspect,
    )
    request = _build_request(
        model="fake-model",
        purpose="decide",
        context={
            "messages": [
                {"role": "system", "content": "budgeted system context"},
                {
                    "role": "user",
                    "content": "current input … compacted",
                    "meta": {
                        "block_kind": "recent_window",
                        "segment_ids": ["turn:current"],
                    },
                },
            ],
            "turns": [
                {
                    "turn_id": "trimmed",
                    "role": "user",
                    "content": "trimmed from the canonical pack",
                    "attachments": [trimmed_ref],
                },
                {
                    "turn_id": "current",
                    "role": "user",
                    "content": "current input with a much longer durable body",
                    "attachments": [current_ref],
                },
            ],
        },
        schema=type("Decision", (), {}),
        temperature=0.0,
    )

    assert [
        message.content for message in request.messages if message.role == "user"
    ] == ["current input … compacted"]
    assert all(
        "trimmed from the canonical pack" not in str(message.content or "")
        for message in request.messages
    )
    current_message = next(
        message for message in request.messages if message.role == "user"
    )
    current_images = [
        part
        for part in current_message.content_parts
        if getattr(part, "source", "") == "artifact"
    ]
    assert [part.artifact_ref for part in current_images] == [current_ref]
    assert inspected == [current_ref]


def test_build_request_keeps_current_image_with_selected_history(monkeypatch) -> None:
    _patch_tool_bundle(monkeypatch)
    prior_ref = "artifact://sha256/" + "a" * 64
    current_ref = "artifact://sha256/" + "b" * 64
    monkeypatch.setattr(
        "openminion.modules.brain.adapters.llm.request.inspect_artifact_image",
        lambda _ref: ("image/png", 1),
    )
    request = _build_request(
        model="fake-model",
        purpose="decide",
        context={
            "messages": [
                {
                    "role": "user",
                    "content": "selected history",
                    "meta": {"segment_ids": ["turn:prior"]},
                },
                {
                    "role": "assistant",
                    "content": "history response",
                    "meta": {"segment_ids": ["turn:reply"]},
                },
                {
                    "role": "user",
                    "content": "mission objective",
                    "meta": {"segment_ids": ["turn_input"]},
                },
            ],
            "turns": [
                {
                    "turn_id": "prior",
                    "role": "user",
                    "content": "selected history",
                    "attachments": [prior_ref],
                },
                {
                    "turn_id": "current",
                    "role": "user",
                    "content": "original user input",
                    "attachments": [current_ref],
                },
            ],
        },
        schema=type("Decision", (), {}),
        temperature=0.0,
    )

    images = {
        message.content: [
            part.artifact_ref
            for part in message.content_parts
            if getattr(part, "source", "") == "artifact"
        ]
        for message in request.messages
        if message.role == "user"
    }
    assert images == {
        "selected history": [prior_ref],
        "mission objective": [current_ref],
    }


def test_text_only_current_turn_keeps_prior_images_historical(monkeypatch) -> None:
    _patch_tool_bundle(monkeypatch)
    refs = [f"artifact://sha256/{value * 64}" for value in "abcde"]
    inspected: list[str] = []

    def _inspect(ref: str):
        inspected.append(ref)
        return "image/png", 1

    monkeypatch.setattr(
        "openminion.modules.brain.adapters.llm.request.inspect_artifact_image",
        _inspect,
    )
    request = _build_request(
        model="fake-model",
        purpose="decide",
        context={
            "messages": [
                {
                    "role": "user",
                    "content": "prior attached turn",
                    "meta": {"segment_ids": ["turn:prior"]},
                },
                {
                    "role": "user",
                    "content": "current text only",
                    "meta": {"segment_ids": ["turn:current"]},
                },
            ],
            "turns": [
                {
                    "turn_id": "prior",
                    "role": "user",
                    "content": "prior attached turn",
                    "attachments": refs,
                },
                {
                    "turn_id": "current",
                    "role": "user",
                    "content": "current text only",
                    "attachments": [],
                },
            ],
        },
        schema=type("Decision", (), {}),
        temperature=0.0,
    )

    prior = next(
        message
        for message in request.messages
        if message.content == "prior attached turn"
    )
    assert [
        part.artifact_ref
        for part in prior.content_parts
        if getattr(part, "source", "") == "artifact"
    ] == refs[:4]
    assert inspected == refs[:4]


def test_build_request_uses_internal_context_alias_without_exposing_it(
    monkeypatch,
) -> None:
    _patch_tool_bundle(monkeypatch)
    refs = [f"artifact://sha256/{value * 64}" for value in "ab"]
    monkeypatch.setattr(
        "openminion.modules.brain.adapters.llm.request.inspect_artifact_image",
        lambda _ref: ("image/png", 1),
    )
    request = _build_request(
        model="fake-model",
        purpose="decide",
        context={
            "messages": [
                {
                    "role": "user",
                    "content": "same request",
                    "meta": {"segment_ids": ["turn:gateway-old"]},
                },
                {
                    "role": "user",
                    "content": "same request",
                    "meta": {"segment_ids": ["turn:gateway-new"]},
                },
                {
                    "role": "user",
                    "content": "current",
                    "meta": {"segment_ids": ["turn:gateway-current"]},
                },
            ],
            "turns": [
                {
                    "turn_id": "brain-old",
                    "context_segment_id": "gateway-old",
                    "role": "user",
                    "content": "same request",
                    "attachments": [refs[0]],
                },
                {
                    "turn_id": "brain-new",
                    "context_segment_id": "gateway-new",
                    "role": "user",
                    "content": "same request",
                    "attachments": [refs[1]],
                },
                {
                    "turn_id": "brain-current",
                    "context_segment_id": "gateway-current",
                    "role": "user",
                    "content": "current",
                    "attachments": [],
                },
            ],
        },
        schema=type("Decision", (), {}),
        temperature=0.0,
    )

    same_messages = [
        message for message in request.messages if message.content == "same request"
    ]
    assert [
        [
            part.artifact_ref
            for part in message.content_parts
            if getattr(part, "source", "") == "artifact"
        ]
        for message in same_messages
    ] == [[refs[0]], [refs[1]]]
    provider_visible = str(
        [message.model_dump(mode="json") for message in request.messages]
    )
    assert "context_segment_id" not in provider_visible
    assert "gateway-old" not in provider_visible
    assert "gateway-new" not in provider_visible
    assert "gateway-current" not in provider_visible


@pytest.mark.parametrize(
    "turns,messages",
    [
        (
            [
                {
                    "turn_id": "brain-old",
                    "context_segment_id": "duplicate",
                    "role": "user",
                    "content": "old",
                    "attachments": ["artifact://sha256/" + "a" * 64],
                },
                {
                    "turn_id": "brain-new",
                    "context_segment_id": "duplicate",
                    "role": "user",
                    "content": "new",
                    "attachments": ["artifact://sha256/" + "b" * 64],
                },
                {
                    "turn_id": "brain-current",
                    "role": "user",
                    "content": "current",
                    "attachments": [],
                },
            ],
            [
                {
                    "role": "user",
                    "content": "old",
                    "meta": {"segment_ids": ["turn:duplicate"]},
                },
                {
                    "role": "user",
                    "content": "new",
                    "meta": {"segment_ids": ["turn:duplicate"]},
                },
                {"role": "user", "content": "current"},
            ],
        ),
        (
            [
                {
                    "turn_id": "brain-history",
                    "context_segment_id": "shared",
                    "role": "user",
                    "content": "history",
                    "attachments": ["artifact://sha256/" + "c" * 64],
                },
                {
                    "turn_id": "shared",
                    "role": "user",
                    "content": "current",
                    "attachments": ["artifact://sha256/" + "d" * 64],
                },
            ],
            [
                {
                    "role": "user",
                    "content": "history",
                    "meta": {"segment_ids": ["turn:shared"]},
                },
                {"role": "user", "content": "current"},
            ],
        ),
    ],
    ids=["duplicate-alias", "alias-canonical-collision"],
)
def test_build_request_rejects_colliding_attachment_keys_before_inspection(
    monkeypatch, turns, messages
) -> None:
    _patch_tool_bundle(monkeypatch)
    inspected: list[str] = []
    monkeypatch.setattr(
        "openminion.modules.brain.adapters.llm.request.inspect_artifact_image",
        lambda ref: (inspected.append(ref) or "image/png", 1),
    )

    with pytest.raises(LLMCtlError, match="could not be aligned") as exc_info:
        _build_request(
            model="fake-model",
            purpose="decide",
            context={"messages": messages, "turns": turns},
            schema=type("Decision", (), {}),
            temperature=0.0,
        )

    assert exc_info.value.code == "INVALID_ARGUMENT"
    assert inspected == []


def test_build_request_rejects_retained_attachment_role_mismatch(
    monkeypatch,
) -> None:
    _patch_tool_bundle(monkeypatch)
    inspected: list[str] = []
    monkeypatch.setattr(
        "openminion.modules.brain.adapters.llm.request.inspect_artifact_image",
        lambda ref: (inspected.append(ref) or "image/png", 1),
    )

    with pytest.raises(LLMCtlError, match="could not be aligned") as exc_info:
        _build_request(
            model="fake-model",
            purpose="decide",
            context={
                "messages": [
                    {
                        "role": "assistant",
                        "content": "wrong role",
                        "meta": {"segment_ids": ["turn:prior"]},
                    },
                    {
                        "role": "user",
                        "content": "current",
                        "meta": {"segment_ids": ["turn:current"]},
                    },
                ],
                "turns": [
                    {
                        "turn_id": "prior",
                        "role": "user",
                        "content": "prior",
                        "attachments": ["artifact://sha256/" + "e" * 64],
                    },
                    {
                        "turn_id": "current",
                        "role": "user",
                        "content": "current",
                        "attachments": [],
                    },
                ],
            },
            schema=type("Decision", (), {}),
            temperature=0.0,
        )

    assert exc_info.value.code == "INVALID_ARGUMENT"
    assert inspected == []


def test_build_request_rejects_reordered_attachment_alignment(monkeypatch) -> None:
    _patch_tool_bundle(monkeypatch)
    inspected: list[str] = []
    monkeypatch.setattr(
        "openminion.modules.brain.adapters.llm.request.inspect_artifact_image",
        lambda ref: (inspected.append(ref) or "image/png", 1),
    )

    with pytest.raises(LLMCtlError, match="could not be aligned") as exc_info:
        _build_request(
            model="fake-model",
            purpose="decide",
            context={
                "messages": [
                    {
                        "role": "user",
                        "content": "new",
                        "meta": {"segment_ids": ["turn:new"]},
                    },
                    {
                        "role": "user",
                        "content": "old",
                        "meta": {"segment_ids": ["turn:old"]},
                    },
                    {"role": "user", "content": "current"},
                ],
                "turns": [
                    {
                        "turn_id": "old",
                        "role": "user",
                        "content": "old",
                        "attachments": ["artifact://sha256/" + "f" * 64],
                    },
                    {
                        "turn_id": "new",
                        "role": "user",
                        "content": "new",
                        "attachments": ["artifact://sha256/" + "1" * 64],
                    },
                    {
                        "turn_id": "current",
                        "role": "user",
                        "content": "current",
                        "attachments": [],
                    },
                ],
            },
            schema=type("Decision", (), {}),
            temperature=0.0,
        )

    assert exc_info.value.code == "INVALID_ARGUMENT"
    assert inspected == []


def test_build_request_rejects_two_attachment_keys_on_one_message(
    monkeypatch,
) -> None:
    _patch_tool_bundle(monkeypatch)
    inspected: list[str] = []
    monkeypatch.setattr(
        "openminion.modules.brain.adapters.llm.request.inspect_artifact_image",
        lambda ref: (inspected.append(ref) or "image/png", 1),
    )

    with pytest.raises(LLMCtlError, match="could not be aligned") as exc_info:
        _build_request(
            model="fake-model",
            purpose="decide",
            context={
                "messages": [
                    {
                        "role": "user",
                        "content": "combined history",
                        "meta": {"segment_ids": ["turn:old-a", "turn:old-b"]},
                    },
                    {"role": "user", "content": "current"},
                ],
                "turns": [
                    {
                        "turn_id": "old-a",
                        "role": "user",
                        "content": "first history",
                        "attachments": ["artifact://sha256/" + "4" * 64],
                    },
                    {
                        "turn_id": "old-b",
                        "role": "user",
                        "content": "second history",
                        "attachments": ["artifact://sha256/" + "5" * 64],
                    },
                    {
                        "turn_id": "current",
                        "role": "user",
                        "content": "current",
                        "attachments": [],
                    },
                ],
            },
            schema=type("Decision", (), {}),
            temperature=0.0,
        )

    assert exc_info.value.code == "INVALID_ARGUMENT"
    assert inspected == []


@pytest.mark.parametrize("current_has_image", [True, False])
def test_build_request_keeps_current_identity_at_latest_user_before_inspection(
    monkeypatch, current_has_image
) -> None:
    _patch_tool_bundle(monkeypatch)
    inspected: list[str] = []
    monkeypatch.setattr(
        "openminion.modules.brain.adapters.llm.request.inspect_artifact_image",
        lambda ref: (inspected.append(ref) or "image/png", 1),
    )
    current_ref = "artifact://sha256/" + "2" * 64
    history_ref = "artifact://sha256/" + "3" * 64
    if current_has_image:
        messages = [
            {
                "role": "user",
                "content": "earlier claimant",
                "meta": {"segment_ids": ["turn:current"]},
            },
            {"role": "user", "content": "actual current"},
        ]
        turns = [
            {
                "turn_id": "current",
                "role": "user",
                "content": "actual current",
                "attachments": [current_ref],
            }
        ]
    else:
        messages = [
            {
                "role": "user",
                "content": "current",
                "meta": {"segment_ids": ["turn:current"]},
            },
            {
                "role": "user",
                "content": "misordered history",
                "meta": {"segment_ids": ["turn:history"]},
            },
        ]
        turns = [
            {
                "turn_id": "history",
                "role": "user",
                "content": "misordered history",
                "attachments": [history_ref],
            },
            {
                "turn_id": "current",
                "role": "user",
                "content": "current",
                "attachments": [],
            },
        ]

    with pytest.raises(LLMCtlError, match="could not be aligned") as exc_info:
        _build_request(
            model="fake-model",
            purpose="decide",
            context={"messages": messages, "turns": turns},
            schema=type("Decision", (), {}),
            temperature=0.0,
        )

    assert exc_info.value.code == "INVALID_ARGUMENT"
    assert inspected == []


def test_build_request_rejects_raw_prefixed_context_alias(monkeypatch) -> None:
    _patch_tool_bundle(monkeypatch)
    ref = "artifact://sha256/" + "a" * 64
    inspected: list[str] = []
    monkeypatch.setattr(
        "openminion.modules.brain.adapters.llm.request.inspect_artifact_image",
        lambda value: (inspected.append(value) or "image/png", 1),
    )
    request = _build_request(
        model="fake-model",
        purpose="decide",
        context={
            "messages": [
                {
                    "role": "user",
                    "content": "historical",
                    "meta": {"segment_ids": ["turn:gateway-historical"]},
                },
                {
                    "role": "user",
                    "content": "current",
                    "meta": {"segment_ids": ["turn:gateway-current"]},
                },
            ],
            "turns": [
                {
                    "turn_id": "brain-historical",
                    "context_segment_id": "turn:gateway-historical",
                    "role": "user",
                    "content": "historical",
                    "attachments": [ref],
                },
                {
                    "turn_id": "brain-current",
                    "role": "user",
                    "content": "current",
                    "attachments": [],
                },
            ],
        },
        schema=type("Decision", (), {}),
        temperature=0.0,
    )

    assert inspected == []
    assert all(
        getattr(part, "source", "") != "artifact"
        for message in request.messages
        for part in message.content_parts
    )


def test_initial_brain_user_turn_persists_attachments_once(monkeypatch) -> None:
    from openminion.modules.brain.runner.tick.context import build_tick_run_context
    from openminion.modules.brain.runner.tick import input_processing

    appended: list[tuple[tuple[object, ...], dict[str, object]]] = []
    session_api = SimpleNamespace(
        append_turn=lambda *args, **kwargs: appended.append((args, kwargs))
    )
    runner = SimpleNamespace(session_api=session_api)
    state = SimpleNamespace(
        trace_id=None,
        status="waiting_user",
        pending_llm_clarify_context=None,
        unresolved_clarify_items=[],
    )
    refs = ["artifact://sha256/" + "a" * 64]
    tick_ctx = build_tick_run_context(
        session_id="session-1",
        user_input="inspect",
        attachments=refs,
        trace_id="trace-1",
        forced_tools=None,
        capability_category=None,
    )
    monkeypatch.setattr(input_processing, "_interpret_user_input", lambda **_kw: None)
    monkeypatch.setattr(
        input_processing, "set_status_unchecked", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        input_processing,
        "_runner_delegate",
        lambda name, *_a, **_k: False if name == "_clarify" else None,
    )

    input_processing.process_user_input(
        runner=runner,
        state=state,
        logger=SimpleNamespace(),
        tick_ctx=tick_ctx,
    )

    assert len(appended) == 1
    assert appended[0][0][:3] == ("session-1", "user", "inspect")
    assert appended[0][1]["attachments"] == refs


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
