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
from openminion.api.server.auth import authorize_ipc_request
from openminion.modules.policy.models import PolicyConfig, PolicyControlError
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
        "trace_id": "t-1",
        "session_id": "s-1",
    }


@pytest.fixture
def fake_runtime():
    runtime = MagicMock()
    runtime.runtime_manager.get_turn_handle.return_value.session_id = "s-1"
    runtime.runtime_manager.get_turn_handle.return_value.resolve_approval.return_value = True
    runtime.action_policy = MagicMock()
    return runtime


def _use_runtime(monkeypatch, runtime) -> None:
    monkeypatch.setattr(
        "openminion.api.operations.approve_pending.resolve_runtime_manager",
        lambda *, config_path, runtime: (runtime.runtime_manager, runtime, False),
    )


@pytest.mark.parametrize("decision", ["allow_once", "deny"])
def test_process_resolves_exact_active_approval(fake_runtime, decision, monkeypatch):
    _use_runtime(monkeypatch, fake_runtime)
    result = process_approval_decision(
        config_path=None,
        runtime=fake_runtime,
        body=_well_formed_body(decision=decision),
    )

    assert result["ok"] is True
    assert result["session_id"] == "s-1"
    assert result["trace_id"] == "t-1"
    assert result["approval_id"] == "ap_abc123"
    assert result["decision"] == decision
    assert result["outcome"] == ("applied" if decision == "allow_once" else "denied")
    assert isinstance(result["resolved_at"], str)
    fake_runtime.runtime_manager.get_turn_handle.assert_called_once_with("t-1")
    fake_runtime.runtime_manager.get_turn_handle.return_value.resolve_approval.assert_called_once_with(
        approval_id="ap_abc123",
        decision=decision,
    )
    fake_runtime.action_policy.resolve_confirmation.assert_not_called()


def test_active_approval_miss_is_terminal_without_policy_fallback(
    fake_runtime, monkeypatch
):
    fake_runtime.runtime_manager.get_turn_handle.return_value.resolve_approval.return_value = False
    _use_runtime(monkeypatch, fake_runtime)

    result = process_approval_decision(
        config_path=None,
        runtime=fake_runtime,
        body=_well_formed_body(),
    )

    assert result == {
        "ok": False,
        "error": {
            "code": "APPROVAL_NOT_ACTIVE",
            "message": "The approval request is not active.",
            "retryable": False,
            "details": {
                "session_id": "s-1",
                "trace_id": "t-1",
                "approval_id": "ap_abc123",
            },
        },
    }
    fake_runtime.action_policy.resolve_confirmation.assert_not_called()
    fake_runtime.action_policy.create_grant_from_confirmation.assert_not_called()


@pytest.mark.parametrize("miss", ["trace", "session"])
def test_active_approval_identity_mismatch_never_resolves(
    fake_runtime, monkeypatch, miss
):
    handle = fake_runtime.runtime_manager.get_turn_handle.return_value
    if miss == "trace":
        fake_runtime.runtime_manager.get_turn_handle.return_value = None
    else:
        handle.session_id = "different-session"
    _use_runtime(monkeypatch, fake_runtime)

    result = process_approval_decision(
        config_path=None,
        runtime=fake_runtime,
        body=_well_formed_body(),
    )

    assert result["error"]["code"] == "APPROVAL_NOT_ACTIVE"
    handle.resolve_approval.assert_not_called()
    fake_runtime.action_policy.resolve_confirmation.assert_not_called()


@pytest.mark.parametrize("missing_field", ["session_id", "trace_id"])
def test_interactive_shape_requires_complete_identity(
    fake_runtime, monkeypatch, missing_field
):
    _use_runtime(monkeypatch, fake_runtime)
    body = _well_formed_body()
    body.pop(missing_field)

    result = process_approval_decision(
        config_path=None,
        runtime=fake_runtime,
        body=body,
    )

    assert result["error"]["code"] == "INVALID_REQUEST"
    assert result["error"]["details"]["field"] == missing_field
    fake_runtime.runtime_manager.get_turn_handle.assert_not_called()
    fake_runtime.action_policy.resolve_confirmation.assert_not_called()


@pytest.mark.parametrize("decision", ["allow_session", "allow_forever"])
def test_interactive_approval_rejects_long_lived_decisions(
    fake_runtime, monkeypatch, decision
):
    _use_runtime(monkeypatch, fake_runtime)

    result = process_approval_decision(
        config_path=None,
        runtime=fake_runtime,
        body=_well_formed_body(decision),
    )

    assert result["error"]["code"] == "INVALID_DECISION"
    assert result["error"]["details"]["choices"] == ["allow_once", "deny"]
    fake_runtime.runtime_manager.get_turn_handle.assert_not_called()


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
        lambda *, config_path, runtime: (runtime.runtime_manager, runtime, False),
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
        lambda *, config_path, runtime: (runtime.runtime_manager, runtime, False),
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
    _use_runtime(monkeypatch, fake_runtime)
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
    assert result.payload["approval_id"] == "ap_abc123"
    assert result.payload["trace_id"] == "t-1"


def test_approval_resume_route_rejects_untyped_decision(fake_runtime, monkeypatch):
    _use_runtime(monkeypatch, fake_runtime)
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
            "session_id": "s-1",
            "trace_id": "t-1",
            "approval_id": "ap_abc123",
            "decision": "deny",
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
    _use_runtime(monkeypatch, fake_runtime)
    body = _well_formed_body(decision="yes please")
    result = process_approval_decision(
        config_path=None, runtime=fake_runtime, body=body
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_DECISION"
    assert "choices" in result["error"]["details"]
    assert result["error"]["details"]["choices"] == list(APPROVAL_CHOICES)
    fake_runtime.action_policy.create_grant_from_confirmation.assert_not_called()


@pytest.mark.parametrize("missing_field", ["approval_id"])
def test_process_rejects_missing_required_field(
    fake_runtime, monkeypatch, missing_field
):
    _use_runtime(monkeypatch, fake_runtime)
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
    _use_runtime(monkeypatch, fake_runtime)
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
    manager = MagicMock()
    monkeypatch.setattr(
        "openminion.api.operations.approve_pending.resolve_runtime_manager",
        lambda *, config_path, runtime: (manager, runtime, False),
    )
    result = process_approval_decision(
        config_path=None,
        runtime=runtime,
        body={"approval_id": "server-approval", "decision": "allow_once"},
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "POLICY_UNAVAILABLE"


def test_unbound_caller_invocation_never_creates_grant(fake_runtime, monkeypatch):
    error = PolicyControlError(
        "PENDING_CONFIRMATION_NOT_FOUND",
        "Pending confirmation was not found.",
    )
    fake_runtime.action_policy.resolve_confirmation.side_effect = error
    _use_runtime(monkeypatch, fake_runtime)
    body = {
        "approval_id": "unknown-approval",
        "decision": "allow_once",
        "invocation": {
            "tool": "exec",
            "method": "run",
            "args": {"command": "echo hello"},
        },
        "ctx": {"session_id": "caller-supplied"},
    }

    result = process_approval_decision(
        config_path=None, runtime=fake_runtime, body=body
    )

    assert result["error"]["code"] == "PENDING_CONFIRMATION_NOT_FOUND"
    fake_runtime.action_policy.resolve_confirmation.assert_called_once_with(
        "unknown-approval", "allow_once"
    )
    fake_runtime.action_policy.create_grant_from_confirmation.assert_not_called()


def test_configured_ipc_auth_rejects_resume_before_route_dispatch() -> None:
    handler = MagicMock()
    handler.ipc_token = "desktop-secret"
    handler.headers = {}

    authorized = authorize_ipc_request(
        handler,
        method="POST",
        path="/v1/approvals/resume",
        request_id="approval-auth-test",
        started_at=0.0,
    )

    assert authorized is False
    status, payload = handler._write_json.call_args.args
    assert status == HTTPStatus.UNAUTHORIZED
    assert payload["error"]["code"] == "ipc_auth_required"


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
    _use_runtime(monkeypatch, fake_runtime)
    body = _well_formed_body(decision=prose_decision)
    result = process_approval_decision(
        config_path=None, runtime=fake_runtime, body=body
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_DECISION"
    fake_runtime.action_policy.create_grant_from_confirmation.assert_not_called()
