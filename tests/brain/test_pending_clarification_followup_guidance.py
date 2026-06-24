from __future__ import annotations

import re

import pytest

from openminion.modules.brain.adapters.llm.request import (
    _build_pending_conversational_clarification_followup_guidance_message,
)
from openminion.modules.brain.schemas.decisions import DecisionAdapter


class _NotDecision:
    pass


def _hint() -> dict[str, object]:
    return {
        "original_user_input": "what's the weather today?",
        "inferred_goal": "weather lookup",
        "known_context": {},
        "unresolved_question": "Which city should I check the weather for?",
        "clarify_question": "Which city should I check the weather for?",
        "user_reply": "my location",
    }


def test_builder_returns_non_empty_when_hint_set() -> None:
    hints = {"pending_conversational_clarification": _hint()}

    message = _build_pending_conversational_clarification_followup_guidance_message(
        purpose="decide",
        schema=DecisionAdapter,
        hints=hints,
    )

    assert message
    # Pin the anchoring instructions so the contract drift is caught by a
    # test rather than by behavior change.
    assert "pending_conversational_clarification" in message
    assert "unresolved_question" in message
    assert "original_user_input" in message
    # The "do not treat as fresh request" anchor is the key anti-LLM
    # safeguard: it tells the model to interpret short replies as answers,
    # not as new commands.
    assert "fresh" in message or "standalone" in message


def test_builder_returns_empty_when_hint_absent() -> None:
    # Missing key.
    message = _build_pending_conversational_clarification_followup_guidance_message(
        purpose="decide",
        schema=DecisionAdapter,
        hints={},
    )
    assert message == ""

    # Present but empty dict.
    message_empty = (
        _build_pending_conversational_clarification_followup_guidance_message(
            purpose="decide",
            schema=DecisionAdapter,
            hints={"pending_conversational_clarification": {}},
        )
    )
    assert message_empty == ""

    message_bad_type = (
        _build_pending_conversational_clarification_followup_guidance_message(
            purpose="decide",
            schema=DecisionAdapter,
            hints={
                "pending_conversational_clarification": "not-a-dict",
            },
        )
    )
    assert message_bad_type == ""


@pytest.mark.parametrize(
    "purpose",
    ["plan", "act", "judge", "reflect", "validate", "respond", ""],
)
def test_builder_returns_empty_for_non_decide_purpose(purpose: str) -> None:
    hints = {"pending_conversational_clarification": _hint()}

    message = _build_pending_conversational_clarification_followup_guidance_message(
        purpose=purpose,
        schema=DecisionAdapter,
        hints=hints,
    )

    assert message == ""


def test_builder_returns_empty_for_non_decision_schema() -> None:
    hints = {"pending_conversational_clarification": _hint()}

    message = _build_pending_conversational_clarification_followup_guidance_message(
        purpose="decide",
        schema=_NotDecision,
        hints=hints,
    )

    assert message == ""


def test_builder_returns_empty_when_hints_is_none() -> None:
    message = _build_pending_conversational_clarification_followup_guidance_message(
        purpose="decide",
        schema=DecisionAdapter,
        hints=None,
    )

    assert message == ""


def test_builder_does_not_inject_keyword_anchoring_rules() -> None:
    hints = {"pending_conversational_clarification": _hint()}

    message = _build_pending_conversational_clarification_followup_guidance_message(
        purpose="decide",
        schema=DecisionAdapter,
        hints=hints,
    )

    lowered = message.lower()
    forbidden_phrases = [
        "my location",
        "the first one",
        "the second one",
        "that one",
        "yes",
        "no",
    ]
    for phrase in forbidden_phrases:
        pattern = re.compile(rf"\b{re.escape(phrase)}\b")
        assert not pattern.search(lowered), (
            f"Anti-LLM: guidance must not enumerate phrase {phrase!r}; "
            "anchoring is structural, not phrase-based."
        )
