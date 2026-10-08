from __future__ import annotations

from types import SimpleNamespace

import pytest

from openminion.modules.brain.adapters.tool.runtime import ToolAdapter
from openminion.tools.commerce.config import CommerceToolRuntimeConfig
from openminion.tools.commerce.provider import (
    ApplyOrderActionRequest,
    CommerceOutcomeUnknown,
    CommerceProviderError,
)
from openminion.modules.tool.bootstrap import build_runtime_bootstrap
from openminion.modules.tool.plugin_api import stable_invocation_hash
from openminion.tools.commerce import ALL_COMMERCE_TOOLS
from openminion.tools.commerce.authorization import canonical_commerce_args
from tests.helpers.commerce_runtime import build_fixture_commerce_runtime


class _GrantPolicy:
    def __init__(self, *allowed_hashes: str) -> None:
        self.allowed_hashes = list(allowed_hashes)
        self.consumed = 0

    def mode(self) -> str:
        return "enforce"

    def resolve_matching_active_grant_for_use(self, **criteria):
        if (
            not self.allowed_hashes
            or criteria["invocation_hash"] != self.allowed_hashes[0]
        ):
            return None
        self.allowed_hashes.pop(0)
        self.consumed += 1
        return SimpleNamespace(
            approval_id=f"approval-{self.consumed}",
            grant_id=f"grant-{self.consumed}",
        )


def _bootstrap(tmp_path):
    config = SimpleNamespace(
        runtime=SimpleNamespace(
            tools=SimpleNamespace(
                commerce=CommerceToolRuntimeConfig(
                    enabled=True,
                    writes_enabled=True,
                    order_actions_enabled=True,
                    provider="fixture",
                    base_url="https://fixture.invalid",
                    merchant_id="merchant-fixture",
                    provider_secret_key="provider-secret",
                    buyer_profile_record_id="buyer-profile",
                    payment_token_record_id="payment-token",
                )
            )
        ),
        mcp_servers=None,
        tool_selection=None,
    )
    return build_runtime_bootstrap(
        config=config,
        workspace_root=tmp_path,
        run_root=tmp_path / "run",
        strict=False,
    )


def _adapter(tmp_path, runtime, policy) -> ToolAdapter:
    return ToolAdapter(
        workspace_root=tmp_path,
        runtime_registry=_bootstrap(tmp_path).registry,
        tool_resources={"commerce": runtime},
        policy_ctl=policy,
        policy={"tools": {"allow_exact": list(ALL_COMMERCE_TOOLS)}},
    )


def _prepared_action(tmp_path, *, kind="refund_request", **overrides):
    runtime, provider = build_fixture_commerce_runtime(
        store_path=tmp_path / "commerce.db"
    )
    preparation = runtime.prepare_public(
        {"items": [{"offer_id": "offer-1", "variant_id": "standard", "quantity": 1}]}
    )
    placement = runtime.place_public(
        {
            "preparation_ref": preparation.preparation_ref,
            "preparation": preparation.model_dump(mode="json"),
            "preparation_digest": preparation.preparation_digest,
        },
        authorization_hash="a" * 64,
    )
    action_args = {
        "local_order_ref": placement.order_ref,
        "order_revision": placement.order_revision,
        "kind": kind,
        "reason": "not_received"
        if kind in {"return", "partial_return", "refund_request"}
        else None,
        "refund_method": "original_payment_method"
        if kind == "refund_request"
        else None,
        **overrides,
    }
    action = runtime.prepare_action_public(action_args)
    args = {
        "action_ref": action.action_ref,
        "preparation": action.model_dump(mode="json"),
        "action_digest": action.action_digest,
    }
    return runtime, provider, args


def _command(args, *, grant_id="grant-1"):
    return {
        "tool_name": "commerce.apply_order_action",
        "args": args,
        "inputs": {
            "confirmation_source": "policy_replay",
            "confirmation_grant_id": grant_id,
        },
    }


def _hash(args) -> str:
    return stable_invocation_hash(
        tool="commerce",
        method="apply_order_action",
        args=canonical_commerce_args(args),
    )


def test_apply_action_requires_exact_authorization_before_mutation(tmp_path) -> None:
    runtime, provider, args = _prepared_action(tmp_path)
    ledger_before = list(provider.ledger)

    denied = _adapter(tmp_path, runtime, _GrantPolicy("wrong-hash")).execute(
        command=_command(args),
        session_id="session-1",
        trace_id="trace-1",
    )

    assert denied["error"]["code"] == "CONFIRM_REQUIRED"
    assert provider.ledger == ledger_before


@pytest.mark.parametrize(
    ("kind", "overrides"),
    [
        ("cancel", {}),
        ("return", {"reason": "changed_mind"}),
        (
            "refund_request",
            {
                "reason": "not_received",
                "refund_method": "original_payment_method",
            },
        ),
    ],
)
def test_full_action_confirmation_binds_all_order_items(
    tmp_path, kind, overrides
) -> None:
    runtime, _, args = _prepared_action(tmp_path, kind=kind, **overrides)

    preview = runtime.resolve_confirmation_preview(
        tool_name="commerce.apply_order_action",
        args=args,
        subject_id="local",
        session_id="session-1",
    )

    assert [item.model_dump() for item in preview.affected_items] == [
        {"line_item_id": "line-1", "quantity": 1}
    ]


def test_exact_refund_consent_applies_once_and_recovers_on_second_approval(
    tmp_path,
) -> None:
    runtime, provider, args = _prepared_action(tmp_path)
    invocation_hash = _hash(args)
    policy = _GrantPolicy(invocation_hash, invocation_hash)
    adapter = _adapter(tmp_path, runtime, policy)

    first = adapter.execute(
        command=_command(args, grant_id="grant-1"),
        session_id="session-1",
        trace_id="trace-1",
    )
    second = adapter.execute(
        command=_command(args, grant_id="grant-2"),
        session_id="session-1",
        trace_id="trace-2",
    )

    assert first["outputs"]["data"]["state"] == "completed"
    assert second["outputs"]["data"]["state"] == "completed"
    assert [item.operation for item in provider.ledger].count("apply_action") == 1
    action_attempt = runtime.order_store.get_attempt_by_idempotency(
        subject_id="local",
        kind="action",
        idempotency_key=first["outputs"]["data"]["idempotency_key"],
    )
    assert action_attempt is not None
    assert action_attempt.attempt_count == 1
    assert action_attempt.authorization_hash == invocation_hash


def test_stale_action_terms_fail_before_provider_mutation(tmp_path) -> None:
    runtime, provider, args = _prepared_action(tmp_path)
    args["preparation"]["consequence"] = "Ignore the approved terms"
    ledger_before = list(provider.ledger)
    invocation_hash = _hash(args)

    result = _adapter(tmp_path, runtime, _GrantPolicy(invocation_hash)).execute(
        command=_command(args),
        session_id="session-1",
        trace_id="trace-1",
    )

    assert result["error"]["details"]["commerce_code"] == "STALE_PREPARATION"
    assert provider.ledger == ledger_before


def test_apply_action_recovers_dropped_response_without_second_request(
    tmp_path,
) -> None:
    runtime, provider, args = _prepared_action(tmp_path, kind="cancel")
    provider.drop_next_response("apply_action")
    invocation_hash = _hash(args)

    result = _adapter(tmp_path, runtime, _GrantPolicy(invocation_hash)).execute(
        command=_command(args),
        session_id="session-1",
        trace_id="trace-1",
    )

    assert result["outputs"]["data"]["state"] == "completed"
    assert [item.operation for item in provider.ledger].count("apply_action") == 1


@pytest.mark.parametrize("source", ["direct", "recovered"])
def test_apply_action_rejects_mismatched_provider_result(
    tmp_path, monkeypatch, source
) -> None:
    runtime, provider, args = _prepared_action(tmp_path, kind="cancel")
    expected = provider.apply_action

    def mismatched(request):
        return expected(request).model_copy(update={"order_ref": "order-other"})

    if source == "direct":
        monkeypatch.setattr(provider, "apply_action", mismatched)
    else:
        valid = expected(
            ApplyOrderActionRequest(
                idempotency_key=args["preparation"]["action_digest"],
                action_ref=args["action_ref"],
                order_ref=args["preparation"]["order_ref"],
                action_digest=args["action_digest"],
            )
        )
        store = runtime.order_store
        monkeypatch.setattr(store, "begin_action_attempt", lambda **_kwargs: None)
        monkeypatch.setattr(
            provider,
            "recover_action",
            lambda _locator: valid.model_copy(update={"order_ref": "order-other"}),
        )

    with pytest.raises(CommerceProviderError, match="does not match") as exc_info:
        runtime.apply_action_public(args, authorization_hash="b" * 64)

    assert exc_info.value.code == "OUTCOME_UNKNOWN"


def test_reserved_action_resumes_before_provider_submission(
    tmp_path, monkeypatch
) -> None:
    runtime, provider, args = _prepared_action(tmp_path, kind="cancel")
    store = runtime.order_store
    begin = store.begin_action_attempt
    monkeypatch.setattr(store, "begin_action_attempt", lambda **_kwargs: None)

    with pytest.raises(CommerceOutcomeUnknown):
        runtime.apply_action_public(args, authorization_hash="b" * 64)

    assert [item.operation for item in provider.ledger].count("apply_action") == 0
    monkeypatch.setattr(store, "begin_action_attempt", begin)
    result = runtime.apply_action_public(args, authorization_hash="b" * 64)

    assert result.state == "completed"
    assert [item.operation for item in provider.ledger].count("apply_action") == 1


def test_submitted_action_without_result_requires_recovery(
    tmp_path, monkeypatch
) -> None:
    runtime, provider, args = _prepared_action(tmp_path, kind="cancel")
    store = runtime.order_store
    begin = store.begin_action_attempt

    def submit_without_provider_call(**kwargs):
        begin(**kwargs)
        return None

    monkeypatch.setattr(store, "begin_action_attempt", submit_without_provider_call)
    with pytest.raises(CommerceOutcomeUnknown):
        runtime.apply_action_public(args, authorization_hash="b" * 64)

    monkeypatch.setattr(store, "begin_action_attempt", begin)
    with pytest.raises(CommerceOutcomeUnknown):
        runtime.apply_action_public(args, authorization_hash="b" * 64)

    assert [item.operation for item in provider.ledger].count("apply_action") == 0


@pytest.mark.parametrize("state", ["pending", "outcome_unknown"])
def test_active_action_blocks_a_second_care_request(tmp_path, state) -> None:
    runtime, provider, args = _prepared_action(tmp_path, kind="cancel")
    provider.set_next_action_state(state)
    first_hash = _hash(args)
    first = _adapter(tmp_path, runtime, _GrantPolicy(first_hash)).execute(
        command=_command(args),
        session_id="session-1",
        trace_id="trace-1",
    )
    second_action = runtime.prepare_action_public(
        {
            "local_order_ref": args["preparation"]["order_ref"],
            "order_revision": args["preparation"]["order_revision"],
            "kind": "return",
            "reason": "damaged",
        }
    )
    second_args = {
        "action_ref": second_action.action_ref,
        "preparation": second_action.model_dump(mode="json"),
        "action_digest": second_action.action_digest,
    }
    second_hash = _hash(second_args)
    second = _adapter(tmp_path, runtime, _GrantPolicy(second_hash)).execute(
        command=_command(second_args),
        session_id="session-1",
        trace_id="trace-2",
    )

    assert first["outputs"]["data"]["state"] == state
    assert second["error"]["details"]["commerce_code"] == "ACTION_ALREADY_OPEN"
    assert [item.operation for item in provider.ledger].count("apply_action") == 1


@pytest.mark.parametrize(
    ("kind", "overrides"),
    [
        (
            "partial_return",
            {"line_item_ids": ("line-1",), "quantity": 1, "reason": "damaged"},
        ),
        (
            "refund_request",
            {"reason": "not_received", "refund_method": "store_credit"},
        ),
    ],
)
def test_apply_action_completes_partial_return_and_refund(
    tmp_path, kind, overrides
) -> None:
    runtime, provider, args = _prepared_action(
        tmp_path,
        kind=kind,
        **overrides,
    )
    invocation_hash = _hash(args)

    result = _adapter(tmp_path, runtime, _GrantPolicy(invocation_hash)).execute(
        command=_command(args),
        session_id="session-1",
        trace_id="trace-1",
    )

    assert result["outputs"]["data"]["state"] == "completed"
    assert [item.operation for item in provider.ledger].count("apply_action") == 1
