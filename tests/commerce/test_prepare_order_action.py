from __future__ import annotations

from types import SimpleNamespace

import pytest

from openminion.tools.commerce.config import CommerceToolRuntimeConfig
from openminion.tools.commerce.provider import CommerceProviderError
from openminion.modules.tool.base import ToolExecutionContext
from openminion.modules.tool.bootstrap import build_runtime_bootstrap
from openminion.modules.tool.runtime.registry_toolspec import execute_tool_spec_call
from tests.helpers.commerce_runtime import build_fixture_commerce_runtime


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


def _placed_runtime(tmp_path):
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
    return runtime, provider, placement


def _call(tmp_path, runtime, arguments):
    return execute_tool_spec_call(
        tool=_bootstrap(tmp_path).registry.get("commerce.prepare_order_action"),
        arguments=arguments,
        context=ToolExecutionContext(
            channel="test",
            target="test",
            session_id="session-1",
            subject_id="local",
            tool_resources={"commerce": runtime},
        ),
    )


@pytest.mark.parametrize(
    ("kind", "extra"),
    [
        ("cancel", {}),
        ("partial_cancel", {"line_item_ids": ["line-1"], "quantity": 1}),
        ("return", {"reason": "changed_mind"}),
        (
            "partial_return",
            {"line_item_ids": ["line-1"], "quantity": 1, "reason": "damaged"},
        ),
        (
            "refund_request",
            {
                "reason": "not_received",
                "refund_method": "original_payment_method",
            },
        ),
    ],
)
def test_prepare_action_supports_closed_kinds_without_mutation(
    tmp_path, kind, extra
) -> None:
    runtime, provider, placement = _placed_runtime(tmp_path)
    ledger_before = list(provider.ledger)

    result = _call(
        tmp_path,
        runtime,
        {
            "local_order_ref": placement.order_ref,
            "order_revision": placement.order_revision,
            "kind": kind,
            **extra,
        },
    )

    assert result.ok is True
    assert result.data["kind"] == kind
    assert result.data["action_revision"].startswith(result.data["action_ref"])
    assert result.data["affected_items"] == [{"line_item_id": "line-1", "quantity": 1}]
    if kind in {"return", "partial_return"}:
        assert result.data["return_method"] == "mail"
        assert result.data["shipment_responsibility"] == "buyer"
        assert result.data["deadlines"] == ["Return by 2030-01-15"]
    assert provider.ledger == ledger_before
    stored = runtime.order_store.get_action_preparation(
        "local", result.data["action_ref"]
    )
    assert stored is not None
    assert stored.prepared.action_digest == result.data["action_digest"]


def test_prepare_refund_binds_exact_destination_and_safe_label(tmp_path) -> None:
    runtime, _, placement = _placed_runtime(tmp_path)
    result = _call(
        tmp_path,
        runtime,
        {
            "local_order_ref": placement.order_ref,
            "order_revision": placement.order_revision,
            "kind": "refund_request",
            "reason": "not_received",
            "refund_method": "original_payment_method",
        },
    )

    assert result.data["refund_destination"] == {
        "destination_digest": "sha256:" + "3" * 64,
        "label": "Visa ending 4242",
    }
    assert result.data["refund"] == {"currency": "USD", "amount_minor": 2500}


def test_prepare_action_rejects_copied_order_before_provider_access(tmp_path) -> None:
    runtime, provider, placement = _placed_runtime(tmp_path)
    inspect_count = len(provider.inspect_calls)

    result = _call(
        tmp_path,
        runtime,
        {
            "local_order_ref": "copied-order",
            "order_revision": placement.order_revision,
            "kind": "cancel",
        },
    )

    assert result.ok is False
    assert result.data["error_code"] == "POLICY_DENIED"
    assert len(provider.inspect_calls) == inspect_count


def test_prepare_action_rejects_stale_revision_and_excess_quantity(tmp_path) -> None:
    runtime, _, placement = _placed_runtime(tmp_path)
    stale = _call(
        tmp_path / "stale",
        runtime,
        {
            "local_order_ref": placement.order_ref,
            "order_revision": "order-1:stale",
            "kind": "cancel",
        },
    )
    excessive = _call(
        tmp_path / "quantity",
        runtime,
        {
            "local_order_ref": placement.order_ref,
            "order_revision": placement.order_revision,
            "kind": "partial_return",
            "line_item_ids": ["line-1"],
            "quantity": 2,
            "reason": "damaged",
        },
    )

    assert stale.data["error_code"] == "INVALID_REQUEST"
    assert stale.data["details"]["commerce_code"] == "STALE_PREPARATION"
    assert excessive.data["error_code"] == "INVALID_REQUEST"
    assert excessive.data["details"]["commerce_code"] == "UNSUPPORTED_ORDER"


def test_prepare_action_arguments_reject_unknown_or_ambiguous_values(tmp_path) -> None:
    runtime, _, placement = _placed_runtime(tmp_path)
    result = _call(
        tmp_path,
        runtime,
        {
            "local_order_ref": placement.order_ref,
            "order_revision": placement.order_revision,
            "kind": "refund_request",
            "reason": "not_received",
            "refund_method": "bank_account",
            "unexpected": True,
        },
    )

    assert result.ok is False
    assert result.data["error_code"] == "invalid_arguments"


def test_prepare_action_rejects_provider_stale_revision(tmp_path) -> None:
    runtime, _, placement = _placed_runtime(tmp_path)
    with pytest.raises(CommerceProviderError, match="stale"):
        runtime.prepare_action_public(
            {
                "local_order_ref": placement.order_ref,
                "order_revision": "order-1:stale",
                "kind": "cancel",
            }
        )


@pytest.mark.parametrize(
    "reason",
    ["unsupported_action", "ambiguous_refund", "return_label_required"],
)
def test_prepare_action_handoff_has_no_mutation_or_persisted_action(
    tmp_path, reason
) -> None:
    runtime, provider, placement = _placed_runtime(tmp_path)
    provider.set_next_action_handoff(reason)
    ledger_before = list(provider.ledger)

    result = _call(
        tmp_path,
        runtime,
        {
            "local_order_ref": placement.order_ref,
            "order_revision": placement.order_revision,
            "kind": "refund_request",
            "reason": "not_received",
            "refund_method": "original_payment_method",
        },
    )

    assert result.ok is True
    assert result.data["state"] == "handoff_required"
    assert result.data["reason_code"] == reason
    assert provider.ledger == ledger_before


@pytest.mark.parametrize(
    "changed",
    [
        {"order_ref": "other-order"},
        {"order_revision": "order-1:other"},
        {"kind": "return"},
        {"reason": "different"},
        {"refund_method": None},
        {"refund_destination": None},
        {"return_method": "mail"},
    ],
)
def test_prepare_action_rejects_mismatched_provider_terms(
    tmp_path, monkeypatch, changed
) -> None:
    runtime, provider, placement = _placed_runtime(tmp_path)
    original = provider.prepare_action

    def mismatched(request):
        return original(request).model_copy(update=changed)

    monkeypatch.setattr(provider, "prepare_action", mismatched)

    with pytest.raises(CommerceProviderError, match="does not match"):
        runtime.prepare_action_public(
            {
                "local_order_ref": placement.order_ref,
                "order_revision": placement.order_revision,
                "kind": "refund_request",
                "reason": "not_received",
                "refund_method": "original_payment_method",
            }
        )


@pytest.mark.parametrize(
    "changed",
    [
        {"return_destination": None},
        {"return_method": None},
        {"shipment_responsibility": None},
        {"deadlines": ()},
    ],
)
def test_prepare_action_requires_complete_return_logistics(
    tmp_path, monkeypatch, changed
) -> None:
    runtime, provider, placement = _placed_runtime(tmp_path)
    original = provider.prepare_action

    def incomplete(request):
        return original(request).model_copy(update=changed)

    monkeypatch.setattr(provider, "prepare_action", incomplete)

    with pytest.raises(CommerceProviderError, match="does not match"):
        runtime.prepare_action_public(
            {
                "local_order_ref": placement.order_ref,
                "order_revision": placement.order_revision,
                "kind": "return",
                "reason": "damaged",
            }
        )
