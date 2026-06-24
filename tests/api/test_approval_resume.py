from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from openminion.api.operations.approve_pending import (
    APPROVAL_CHOICES,
    parse_decision,
    process_approval_decision,
)


# parse_decision: ANTI-LLM gate — typed input only


@pytest.mark.parametrize("typed", APPROVAL_CHOICES)
def test_parse_accepts_each_typed_choice_exactly(typed):
    assert parse_decision(typed) == typed


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("ALLOW_ONCE", "allow_once"),
        ("Allow_Session", "allow_session"),
        ("  allow_forever  ", "allow_forever"),
        ("DENY", "deny"),
    ],
)
def test_parse_normalizes_case_and_whitespace(raw, expected):
    assert parse_decision(raw) == expected


def test_parse_rejects_non_string():
    assert parse_decision(None) is None
    assert parse_decision(123) is None
    assert parse_decision(["allow_once"]) is None
    assert parse_decision({"decision": "allow_once"}) is None


def test_parse_rejects_empty_string():
    assert parse_decision("") is None
    assert parse_decision("   ") is None


@pytest.mark.parametrize(
    "prose",
    [
        "yes",
        "approve",
        "allow",
        "go ahead",
        "yeah ok",
        "allow_once please",
        "I want allow_session",
        "allowonce",
        "allow_one",  # prefix match
        "allow_oncee",  # typo
        "allow once",  # space-separated, NOT exact
    ],
)
def test_parse_rejects_prose_and_near_misses(prose):
    assert parse_decision(prose) is None


# process_approval_decision: full operation flow


def _well_formed_body(decision: str = "allow_once") -> dict:
    return {
        "approval_id": "ap_abc123",
        "decision": decision,
        "invocation": {
            "tool": "exec",
            "method": "run",
            "args": {"command": "echo hello"},
            "invocation_id": "inv_001",
        },
        "ctx": {
            "trace_id": "t-1",
            "session_id": "s-1",
            "agent_id": "a-1",
            "subject_id": "u-1",
            "mode_name": "guided",
        },
    }


@pytest.fixture
def fake_runtime():
    runtime = MagicMock()
    runtime.policyctl = MagicMock()
    runtime.policyctl.create_grant_from_confirmation = MagicMock(
        return_value="gr_test123"
    )
    return runtime


@pytest.mark.parametrize("decision", APPROVAL_CHOICES)
def test_process_creates_grant_for_each_typed_decision(
    fake_runtime, decision, monkeypatch
):
    monkeypatch.setattr(
        "openminion.api.operations.approve_pending.resolve_runtime_manager",
        lambda *, config_path, runtime: (None, runtime, False),
    )
    body = _well_formed_body(decision=decision)
    result = process_approval_decision(
        config_path=None, runtime=fake_runtime, body=body
    )
    assert result["ok"] is True
    assert result["decision"] == decision
    assert result["grant_id"] == "gr_test123"
    fake_runtime.policyctl.create_grant_from_confirmation.assert_called_once()
    call_kwargs = fake_runtime.policyctl.create_grant_from_confirmation.call_args.kwargs
    assert call_kwargs["action"] == decision


def test_process_rejects_non_typed_decision_no_inference(fake_runtime, monkeypatch):
    monkeypatch.setattr(
        "openminion.api.operations.approve_pending.resolve_runtime_manager",
        lambda *, config_path, runtime: (None, runtime, False),
    )
    body = _well_formed_body(decision="yes please")
    result = process_approval_decision(
        config_path=None, runtime=fake_runtime, body=body
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_DECISION"
    assert "choices" in result["error"]["details"]
    assert result["error"]["details"]["choices"] == list(APPROVAL_CHOICES)
    fake_runtime.policyctl.create_grant_from_confirmation.assert_not_called()


@pytest.mark.parametrize("missing_field", ["approval_id", "invocation", "ctx"])
def test_process_rejects_missing_required_field(
    fake_runtime, monkeypatch, missing_field
):
    monkeypatch.setattr(
        "openminion.api.operations.approve_pending.resolve_runtime_manager",
        lambda *, config_path, runtime: (None, runtime, False),
    )
    body = _well_formed_body()
    body.pop(missing_field)
    result = process_approval_decision(
        config_path=None, runtime=fake_runtime, body=body
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_REQUEST"
    assert result["error"]["details"]["field"] == missing_field
    fake_runtime.policyctl.create_grant_from_confirmation.assert_not_called()


def test_process_rejects_non_mapping_body(fake_runtime, monkeypatch):
    monkeypatch.setattr(
        "openminion.api.operations.approve_pending.resolve_runtime_manager",
        lambda *, config_path, runtime: (None, runtime, False),
    )
    result = process_approval_decision(
        config_path=None,
        runtime=fake_runtime,
        body="not a mapping",  # type: ignore[arg-type]
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_REQUEST"


def test_process_returns_policy_unavailable_when_runtime_lacks_policyctl(
    monkeypatch,
):
    runtime = MagicMock(spec=[])  # explicitly no attributes
    monkeypatch.setattr(
        "openminion.api.operations.approve_pending.resolve_runtime_manager",
        lambda *, config_path, runtime: (None, runtime, False),
    )
    result = process_approval_decision(
        config_path=None, runtime=runtime, body=_well_formed_body()
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "POLICY_UNAVAILABLE"


def test_process_invocation_must_be_mapping(fake_runtime, monkeypatch):
    monkeypatch.setattr(
        "openminion.api.operations.approve_pending.resolve_runtime_manager",
        lambda *, config_path, runtime: (None, runtime, False),
    )
    body = _well_formed_body()
    body["invocation"] = "exec.run"  # malformed: string instead of dict
    result = process_approval_decision(
        config_path=None, runtime=fake_runtime, body=body
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_REQUEST"


# ANTI-LLM negative regression: full-pipeline test


@pytest.mark.parametrize(
    "prose_decision",
    [
        "yes",
        "approve it",
        "allow",
        "go ahead",
        "I think allow_session",
        "deny it forever",
        "allow_once and remember",
    ],
)
def test_non_typed_decision_rejected_no_inference(
    fake_runtime, monkeypatch, prose_decision
):
    monkeypatch.setattr(
        "openminion.api.operations.approve_pending.resolve_runtime_manager",
        lambda *, config_path, runtime: (None, runtime, False),
    )
    body = _well_formed_body(decision=prose_decision)
    result = process_approval_decision(
        config_path=None, runtime=fake_runtime, body=body
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_DECISION"
    fake_runtime.policyctl.create_grant_from_confirmation.assert_not_called()
