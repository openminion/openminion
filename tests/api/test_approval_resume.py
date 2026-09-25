from __future__ import annotations

from http import HTTPStatus
from unittest.mock import MagicMock

import pytest

from openminion.api.operations.approve_pending import (
    APPROVAL_CHOICES,
    parse_decision,
    process_approval_decision,
)
from openminion.api.routes.approve_pending import handle_request
from openminion.api.routes.contracts import APIRouteContext
from openminion.api.server import dispatch_request
from openminion.modules.policy.models import PolicyConfig
from openminion.modules.policy.runtime.service import PolicyCtl
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.tools.ops.contracts import OperationTarget
from openminion.tools.ops.registry import TargetRegistry
from openminion.tools.ops.service import OpsService


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
    runtime.action_policy = MagicMock()
    runtime.action_policy.create_grant_from_confirmation = MagicMock(
        return_value="gr_test123"
    )
    return runtime


@pytest.mark.parametrize(
    "decision", [choice for choice in APPROVAL_CHOICES if choice != "deny"]
)
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
    create_grant = fake_runtime.action_policy.create_grant_from_confirmation
    create_grant.assert_called_once()
    assert create_grant.call_args.kwargs["action"] == decision


def test_process_deny_does_not_create_grant(fake_runtime, monkeypatch):
    monkeypatch.setattr(
        "openminion.api.operations.approve_pending.resolve_runtime_manager",
        lambda *, config_path, runtime: (None, runtime, False),
    )

    result = process_approval_decision(
        config_path=None,
        runtime=fake_runtime,
        body=_well_formed_body(decision="deny"),
    )

    assert result == {
        "ok": True,
        "approval_id": "ap_abc123",
        "decision": "deny",
        "grant_id": None,
    }
    fake_runtime.action_policy.create_grant_from_confirmation.assert_not_called()


def test_ops_approval_runs_server_owned_plan_without_caller_invocation(
    monkeypatch,
) -> None:
    policy = PolicyCtl.with_sqlite(":memory:", config=PolicyConfig(mode="enforce"))
    service = OpsService(
        targets=TargetRegistry((OperationTarget(target_id="local", kind="local"),)),
        action_policy=policy,
    )
    plan = service.plan_command(
        target_id="local",
        argv=("printf", "ready"),
        session_id="session-1",
    )
    with pytest.raises(ToolRuntimeError) as pending:
        service.run_plan(
            plan_id=plan.plan_id,
            plan_hash=plan.plan_hash,
            session_id=plan.session_id,
        )
    runtime = MagicMock()
    runtime.action_policy = policy
    runtime.ops_service = service
    monkeypatch.setattr(
        "openminion.api.operations.approve_pending.resolve_runtime_manager",
        lambda *, config_path, runtime: (None, runtime, False),
    )

    result = process_approval_decision(
        config_path=None,
        runtime=runtime,
        body={
            "approval_id": str(pending.value.details["approval_id"]),
            "decision": "allow_once",
        },
    )

    assert result["ok"] is True
    assert result["job"]["status"] == "succeeded"
    assert result["job"]["plan_id"] == plan.plan_id


def test_ops_approval_rejects_long_lived_decision_with_exact_choices(
    monkeypatch,
) -> None:
    policy = PolicyCtl.with_sqlite(":memory:", config=PolicyConfig(mode="enforce"))
    service = OpsService(
        targets=TargetRegistry((OperationTarget(target_id="local", kind="local"),)),
        action_policy=policy,
    )
    plan = service.plan_command(
        target_id="local", argv=("printf", "ready"), session_id="session-1"
    )
    with pytest.raises(ToolRuntimeError) as pending:
        service.run_plan(
            plan_id=plan.plan_id,
            plan_hash=plan.plan_hash,
            session_id=plan.session_id,
        )
    runtime = MagicMock(action_policy=policy, ops_service=service)
    monkeypatch.setattr(
        "openminion.api.operations.approve_pending.resolve_runtime_manager",
        lambda *, config_path, runtime: (None, runtime, False),
    )

    result = process_approval_decision(
        config_path=None,
        runtime=runtime,
        body={
            "approval_id": str(pending.value.details["approval_id"]),
            "decision": "allow_session",
        },
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_DECISION"
    assert result["error"]["details"]["choices"] == ["allow_once", "deny"]


def test_approval_resume_route_exposes_typed_operation(fake_runtime, monkeypatch):
    monkeypatch.setattr(
        "openminion.api.operations.approve_pending.resolve_runtime_manager",
        lambda *, config_path, runtime: (None, runtime, False),
    )
    result = handle_request(
        APIRouteContext(
            config_path=None,
            runtime=fake_runtime,
            runtime_bootstrap_error=None,
            request_headers=None,
            request_id="approval-route-test",
        ),
        method_name="POST",
        path="/v1/approvals/resume",
        body=_well_formed_body(decision="allow_once"),
        query=None,
    )

    assert result is not None
    assert result.status == HTTPStatus.OK
    assert result.payload["grant_id"] == "gr_test123"


def test_approval_resume_route_rejects_untyped_decision(fake_runtime, monkeypatch):
    monkeypatch.setattr(
        "openminion.api.operations.approve_pending.resolve_runtime_manager",
        lambda *, config_path, runtime: (None, runtime, False),
    )
    result = handle_request(
        APIRouteContext(
            config_path=None,
            runtime=fake_runtime,
            runtime_bootstrap_error=None,
            request_headers=None,
            request_id="approval-route-test",
        ),
        method_name="POST",
        path="/v1/approvals/resume",
        body=_well_formed_body(decision="yes"),
        query=None,
    )

    assert result is not None
    assert result.status == HTTPStatus.BAD_REQUEST
    assert result.payload["error"]["code"] == "INVALID_DECISION"


def test_approval_resume_route_is_registered(fake_runtime, monkeypatch):
    monkeypatch.setattr(
        "openminion.api.routes.approve_pending.process_approval_decision",
        lambda **_kwargs: {
            "ok": True,
            "approval_id": "ap_abc123",
            "decision": "deny",
            "grant_id": None,
        },
    )

    status, payload = dispatch_request(
        "POST",
        "/v1/approvals/resume",
        None,
        body=_well_formed_body(decision="deny"),
        runtime=fake_runtime,
    )

    assert status == HTTPStatus.OK
    assert payload["decision"] == "deny"


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
    fake_runtime.action_policy.create_grant_from_confirmation.assert_not_called()


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
    fake_runtime.action_policy.create_grant_from_confirmation.assert_not_called()


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


def test_process_returns_policy_unavailable_when_runtime_lacks_action_policy(
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
    fake_runtime.action_policy.create_grant_from_confirmation.assert_not_called()
