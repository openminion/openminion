from unittest.mock import MagicMock

from openminion.api.operations.approve_pending import process_approval_decision
from openminion.modules.policy.models import PolicyControlError


def _body(decision: str = "allow_once") -> dict:
    return {
        "approval_id": "approval-1",
        "decision": decision,
        "invocation": {
            "tool": "blockchain",
            "method": "send_transaction",
            "args": {"transaction": {"value_wei": "1"}},
        },
        "ctx": {"subject_id": "operator-1"},
    }


def test_exact_blockchain_approval_resolves_server_owned_pending_row(
    monkeypatch,
) -> None:
    runtime = MagicMock()
    runtime.action_policy.resolve_confirmation.return_value = "grant-1"
    monkeypatch.setattr(
        "openminion.api.operations.approve_pending.resolve_runtime_manager",
        lambda *, config_path, runtime: (None, runtime, False),
    )

    result = process_approval_decision(
        config_path=None,
        runtime=runtime,
        body=_body(),
    )

    assert result == {
        "ok": True,
        "approval_id": "approval-1",
        "decision": "allow_once",
        "grant_id": "grant-1",
    }
    runtime.action_policy.resolve_confirmation.assert_called_once_with(
        "approval-1", "allow_once"
    )
    runtime.action_policy.create_grant_from_confirmation.assert_not_called()


def test_exact_blockchain_approval_returns_stable_policy_error(monkeypatch) -> None:
    runtime = MagicMock()
    error = PolicyControlError(
        "PENDING_CONFIRMATION_EXPIRED",
        "Pending confirmation expired.",
    )
    runtime.action_policy.resolve_confirmation.side_effect = error
    monkeypatch.setattr(
        "openminion.api.operations.approve_pending.resolve_runtime_manager",
        lambda *, config_path, runtime: (None, runtime, False),
    )

    result = process_approval_decision(
        config_path=None,
        runtime=runtime,
        body=_body(),
    )

    assert result["error"] == {
        "code": "PENDING_CONFIRMATION_EXPIRED",
        "message": "Pending confirmation expired.",
        "details": {"approval_id": "approval-1"},
    }
