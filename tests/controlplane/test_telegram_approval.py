from __future__ import annotations

import pytest

from openminion.modules.controlplane.channels.telegram.approval import (
    APPROVAL_CHOICES,
    extract_approval_request,
    parse_approval_decision,
    render_approval_prompt,
)


# extract_approval_request: typed extraction only


def _confirm_required_payload(
    *,
    approval_id: str = "ap_abc123",
    choices: list[str] | None = None,
    reason: str = "exec_policy",
    session_id: str = "s-1",
    trace_id: str = "t-1",
) -> dict:
    return {
        "ok": False,
        "session_id": session_id,
        "data": {"trace_id": trace_id},
        "error": {
            "code": "CONFIRM_REQUIRED",
            "message": "Approval required",
            "details": {
                "approval_id": approval_id,
                "choices": list(choices)
                if choices is not None
                else list(APPROVAL_CHOICES),
                "reason": reason,
            },
        },
    }


def test_extract_returns_normalized_dict_on_well_formed_payload():
    payload = _confirm_required_payload()
    result = extract_approval_request(payload)
    assert result is not None
    assert result["approval_id"] == "ap_abc123"
    assert result["choices"] == list(APPROVAL_CHOICES)
    assert result["reason"] == "exec_policy"
    assert result["session_id"] == "s-1"
    assert result["trace_id"] == "t-1"


def test_extract_returns_none_on_payload_without_error_block():
    assert extract_approval_request({"ok": True}) is None
    assert extract_approval_request({"ok": False}) is None
    assert extract_approval_request({"ok": False, "error": "string error"}) is None


def test_extract_returns_none_on_non_confirm_required_error_code():
    payload = _confirm_required_payload()
    payload["error"]["code"] = "POLICY_DENIED"
    assert extract_approval_request(payload) is None


def test_extract_returns_none_when_approval_id_missing():
    payload = _confirm_required_payload(approval_id="")
    assert extract_approval_request(payload) is None


def test_extract_returns_none_when_choices_missing_or_empty():
    payload = _confirm_required_payload(choices=[])
    assert extract_approval_request(payload) is None
    payload = _confirm_required_payload()
    payload["error"]["details"]["choices"] = "allow_once"  # malformed type
    assert extract_approval_request(payload) is None


def test_extract_strips_whitespace_in_choice_strings():
    payload = _confirm_required_payload(
        choices=["  allow_once  ", "allow_session", "", "  ", "deny"]
    )
    result = extract_approval_request(payload)
    assert result is not None
    assert result["choices"] == ["allow_once", "allow_session", "deny"]


# render_approval_prompt: pure rendering, no semantic transformation


def test_render_includes_all_four_typed_choices_by_default():
    request = {
        "approval_id": "ap_abc",
        "choices": list(APPROVAL_CHOICES),
        "reason": "exec_policy",
    }
    rendered = render_approval_prompt(request)
    for choice in APPROVAL_CHOICES:
        assert choice in rendered, f"choice {choice!r} missing from rendered prompt"


def test_render_uses_payload_choices_when_provided():
    request = {
        "approval_id": "ap_abc",
        "choices": ["allow_once", "deny"],
        "reason": "exec_policy",
    }
    rendered = render_approval_prompt(request)
    assert "allow_once" in rendered
    assert "deny" in rendered
    assert "allow_forever" not in rendered


def test_render_includes_approval_id_and_reason_when_present():
    request = {
        "approval_id": "ap_xyz",
        "choices": ["allow_once", "deny"],
        "reason": "exec_policy",
    }
    rendered = render_approval_prompt(request)
    assert "ap_xyz" in rendered
    assert "exec_policy" in rendered


# parse_approval_decision: ANTI-LLM gate — typed input only


@pytest.mark.parametrize("typed", APPROVAL_CHOICES)
def test_parse_accepts_each_typed_choice_exactly(typed):
    assert parse_approval_decision(typed) == typed


@pytest.mark.parametrize(
    "typed",
    ["ALLOW_ONCE", "Allow_Session", "  allow_forever  ", "DENY"],
)
def test_parse_normalizes_case_and_whitespace(typed):
    expected = typed.strip().lower()
    assert parse_approval_decision(typed) == expected


def test_parse_rejects_none():
    assert parse_approval_decision(None) is None


def test_parse_rejects_non_string():
    assert parse_approval_decision(123) is None  # type: ignore[arg-type]
    assert parse_approval_decision(["allow_once"]) is None  # type: ignore[arg-type]


def test_parse_rejects_empty_string():
    assert parse_approval_decision("") is None
    assert parse_approval_decision("    ") is None


# ANTI-LLM negative regressions: prose intent must NEVER produce a typed
# decision. These tests fail-closed if any prose-inference path is added.


@pytest.mark.parametrize(
    "prose",
    [
        "yeah ok do it",
        "yes please",
        "no thanks",
        "approve",
        "allow",
        "go ahead",
        "do it once",
        "session please",
        "yes for this session",
        "sure forever",
        "deny it",
        "stop",
        "allow_once please",  # extra prose around typed token
        "allow_once and remember",
        "I think allow_session",
        "allow once",
        "allowonce",
        "allow_oncee",
        "allow_one",
        "allow",
    ],
)
def test_prose_reply_does_not_route_to_policyctl(prose):
    assert parse_approval_decision(prose) is None


def test_multi_typed_input_rejected():
    assert parse_approval_decision("allow_once or deny") is None
    assert parse_approval_decision("allow_session deny") is None


def test_non_typed_input_does_not_synthesize_a_default():
    assert parse_approval_decision("???") is None
    assert parse_approval_decision("y") is None
    assert parse_approval_decision("yes") is None
