from __future__ import annotations

from pathlib import Path

from openminion.modules.brain.adapters.llm import request as brain_llm_request


# Shared helpers


def _patch_tool_bundle(monkeypatch) -> None:
    class _Bundle:
        system_tools: list = []

    monkeypatch.setattr(
        "openminion.modules.brain.adapters.llm.request"
        "._TOOL_SCHEMA_SERVICE.get_tools_for_purpose",
        lambda **kwargs: _Bundle(),
    )


def _decide_schema() -> type:
    return type("DummySchema", (), {"__name__": "Decision"})


_FORBIDDEN_REQUEST_CALL_PATTERNS = (
    "_build_conversational_clarify_followup_message(",
    "_build_recent_turn_continuity_message(",
    "from openminion.modules.brain.adapters.llm.request import "
    "_build_conversational_clarify_followup_message",
    "from openminion.modules.brain.adapters.llm.request import "
    "_build_recent_turn_continuity_message",
)


_FORBIDDEN_PROSE_FRAGMENTS = (
    "may be answering a clarification",
    "Treat it as a follow-up to the earlier request",
    "unless it clearly starts a different topic",
    "from the same live session",
    "without relying on runtime phrase matching",
)


def test_bcpr03_runtime_prose_builders_are_removed_from_module() -> None:

    assert not hasattr(
        brain_llm_request, "_build_conversational_clarify_followup_message"
    ), (
        "BCPR-03: `_build_conversational_clarify_followup_message` was "
        "removed as a runtime-authored LLM guidance builder. It must not "
        "come back — design a typed clarification-continuation seam "
        "through a new lane instead."
    )
    assert not hasattr(brain_llm_request, "_build_recent_turn_continuity_message"), (
        "BCPR-03: `_build_recent_turn_continuity_message` was removed as "
        "runtime-authored interpretation prose. The LLM already sees the "
        "conversation history; any reintroduction must use a typed "
        "surface."
    )


def test_bcpr03_runtime_prose_builders_are_not_exported() -> None:

    exported = set(getattr(brain_llm_request, "__all__", []))
    assert "_build_recent_turn_continuity_message" not in exported, (
        "BCPR-03: `_build_recent_turn_continuity_message` must not appear "
        "in `request.__all__` — it was retired by BCPR-02."
    )
    # `_build_conversational_clarify_followup_message` was never exported
    # historically, but guard it anyway so a future edit cannot add it back
    # under the same `__all__`.
    assert "_build_conversational_clarify_followup_message" not in exported, (
        "BCPR-03: `_build_conversational_clarify_followup_message` must "
        "not appear in `request.__all__`."
    )


def test_bcpr03_request_module_source_has_no_forbidden_call_patterns() -> None:

    source = Path(brain_llm_request.__file__).read_text(encoding="utf-8")
    for pattern in _FORBIDDEN_REQUEST_CALL_PATTERNS:
        assert pattern not in source, (
            "BCPR-03: `modules/brain/adapters/llm/request.py` must not "
            f"reintroduce the forbidden call pattern {pattern!r}."
        )


def test_bcpr03_build_request_emits_no_runtime_prose(monkeypatch) -> None:

    _patch_tool_bundle(monkeypatch)
    context = {
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "what's weather?"},
            {
                "role": "assistant",
                "content": "Which location's weather would you like to know about?",
            },
            {"role": "user", "content": "china"},
        ],
        "hints": {
            "user_input": "china",
            "pending_conversational_clarification": {
                "original_user_input": "what's weather?",
                "inferred_goal": "",
                "known_context": {},
                "unresolved_question": "Which location's weather would you like to know about?",
                "clarify_question": "Which location's weather would you like to know about?",
                "user_reply": "china",
            },
        },
    }

    request = brain_llm_request._build_request(
        model="fake-model",
        purpose="decide",
        context=context,
        schema=_decide_schema(),
        temperature=0.0,
    )

    all_contents = "\n".join(
        str(getattr(message, "content", "") or "") for message in request.messages
    )
    for fragment in _FORBIDDEN_PROSE_FRAGMENTS:
        assert fragment not in all_contents, (
            f"BCPR-03: `_build_request` produced the removed runtime "
            f"prose fragment {fragment!r}. The prose builders are "
            "retired — do not reintroduce them."
        )
