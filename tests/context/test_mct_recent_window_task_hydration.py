from __future__ import annotations

from openminion.modules.context.schemas import SessionTurn
from openminion.modules.context.segment import (
    protected_decide_recent_turn_indexes,
)


def _turn(turn_id: str, role: str, content: str = "x") -> SessionTurn:
    return SessionTurn(turn_id=turn_id, role=role, content=content)


# Decide purpose, assistant anchor present — UNCHANGED behavior.


def test_decide_with_user_assistant_pair_protects_pair() -> None:
    turns = [
        _turn("t1", "user", "task"),
        _turn("t2", "assistant", "answer"),
    ]
    result = protected_decide_recent_turn_indexes(turns, purpose="decide")
    assert result == {0, 1}


def test_decide_with_trailing_assistant_only_protects_assistant() -> None:
    turns = [
        _turn("t1", "assistant", "answer"),
    ]
    result = protected_decide_recent_turn_indexes(turns, purpose="decide")
    assert result == {0}


def test_decide_scans_from_end_for_last_assistant() -> None:
    turns = [
        _turn("t1", "user", "old-task"),
        _turn("t2", "assistant", "old-answer"),
        _turn("t3", "user", "latest-task"),
        _turn("t4", "assistant", "latest-answer"),
    ]
    result = protected_decide_recent_turn_indexes(turns, purpose="decide")
    assert result == {2, 3}


# MCT-RWTH: orphan-user-turn protection (the post-PCHC/GOPP case).


def test_decide_with_only_user_turn_returns_empty() -> None:
    turns = [
        _turn("t1", "user", "Create a tiny Python project..."),
    ]
    result = protected_decide_recent_turn_indexes(turns, purpose="decide")
    assert result == set()


def test_decide_with_two_user_turns_returns_empty() -> None:
    turns = [
        _turn("t1", "user", "Create a tiny Python project..."),
        _turn("t2", "user", "yes"),
    ]
    result = protected_decide_recent_turn_indexes(turns, purpose="decide")
    assert result == set()


def test_act_purpose_with_orphan_user_turns_protects_anchors() -> None:
    turns = [
        _turn("t1", "user", "Create a tiny Python project..."),
        _turn("t2", "user", "yes"),
    ]
    result = protected_decide_recent_turn_indexes(turns, purpose="act")
    assert result == {0, 1}


def test_entry_purpose_with_single_user_turn_protects_anchor() -> None:
    turns = [
        _turn("t1", "user", "task"),
    ]
    result = protected_decide_recent_turn_indexes(turns, purpose="entry")
    assert result == {0}


def test_judge_purpose_with_orphan_users_protects_anchors() -> None:
    turns = [
        _turn("t1", "user", "task"),
        _turn("t2", "user", "yes"),
    ]
    result = protected_decide_recent_turn_indexes(turns, purpose="judge")
    assert result == {0, 1}


def test_summarize_purpose_with_user_turn_protects_anchor() -> None:
    turns = [
        _turn("t1", "user", "task"),
    ]
    result = protected_decide_recent_turn_indexes(turns, purpose="summarize")
    assert result == {0}


# Mixed shapes and edge cases.


def test_act_purpose_with_assistant_present_returns_empty() -> None:
    turns = [
        _turn("t1", "user", "task"),
        _turn("t2", "assistant", "answer"),
    ]
    result = protected_decide_recent_turn_indexes(turns, purpose="act")
    assert result == set()


def test_decide_with_assistant_then_orphan_users_still_pins_pair() -> None:
    turns = [
        _turn("t1", "user", "old-task"),
        _turn("t2", "assistant", "answer"),
        _turn("t3", "user", "follow-up"),
    ]
    result = protected_decide_recent_turn_indexes(turns, purpose="decide")
    assert result == {0, 1}


def test_empty_recent_turns_returns_empty_for_any_purpose() -> None:
    for purpose in ("decide", "act", "entry", "judge", "summarize", "unknown"):
        assert protected_decide_recent_turn_indexes([], purpose=purpose) == set()


def test_act_purpose_with_only_tool_role_returns_empty() -> None:
    turns = [
        _turn("t1", "tool", '{"status":"ok"}'),
    ]
    result = protected_decide_recent_turn_indexes(turns, purpose="act")
    assert result == set()


def test_act_purpose_with_user_and_tool_protects_user() -> None:
    turns = [
        _turn("t1", "user", "task"),
        _turn("t1b", "tool", '{"status":"ok"}'),
    ]
    result = protected_decide_recent_turn_indexes(turns, purpose="act")
    assert result == {0}


def test_three_user_turns_protects_first_and_last_only() -> None:
    turns = [
        _turn("t1", "user", "original-task"),
        _turn("t2", "user", "intermediate"),
        _turn("t3", "user", "latest"),
    ]
    result = protected_decide_recent_turn_indexes(turns, purpose="act")
    assert result == {0, 2}


# Cross-fix guard: this protection must not depend on the prose content
# (no MiniMax-specific or keyword heuristics).


def test_protection_is_structural_not_prose_based() -> None:
    turns_a = [
        _turn("t1", "user", "Create a tiny Python project..."),
        _turn("t2", "user", "yes"),
    ]
    turns_b = [
        _turn("t1", "user", "Q: hello"),
        _turn("t2", "user", "no"),
    ]
    result_a = protected_decide_recent_turn_indexes(turns_a, purpose="act")
    result_b = protected_decide_recent_turn_indexes(turns_b, purpose="act")
    assert result_a == result_b == {0, 1}


def test_non_decide_purpose_no_assistant_still_protects_via_fallback() -> None:
    turns = [_turn("t1", "user", "task")]
    assert protected_decide_recent_turn_indexes(turns, purpose="not-decide") == {0}
    # Empty list still returns empty regardless of purpose.
    assert protected_decide_recent_turn_indexes([], purpose="not-decide") == set()


def test_decide_purpose_existing_v15_packing_contract_preserved() -> None:
    turns = [_turn("old-user", "user", "old raw turn " * 500)]
    assert protected_decide_recent_turn_indexes(turns, purpose="decide") == set()
