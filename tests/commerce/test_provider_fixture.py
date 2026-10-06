from __future__ import annotations

import pytest
from pydantic import ValidationError

from openminion.modules.commerce.fixture import DroppedCommerceResponse
from openminion.modules.commerce.provider import (
    ActionRecoveryLocator,
    ApplyOrderActionRequest,
    CommerceProviderError,
    InspectRequest,
    PlaceOrderRequest,
    PlacementRecoveryLocator,
    PrepareOrderActionRequest,
    PrepareOrderRequest,
    PreparationRecoveryLocator,
    RequestedItem,
)
from tests.helpers.commerce_runtime import build_fixture_commerce_runtime


def _prepare(*, quantity: int = 1):
    runtime, provider = build_fixture_commerce_runtime()
    request = PrepareOrderRequest(
        idempotency_key="prepare-1",
        items=(
            RequestedItem(
                offer_id="offer-1",
                variant_id="standard",
                quantity=quantity,
            ),
        ),
    )
    return runtime, provider, runtime.prepare_order(request)


def _place(*, quantity: int = 1):
    runtime, provider, preparation = _prepare(quantity=quantity)
    request = PlaceOrderRequest(
        idempotency_key="place-1",
        preparation_ref=preparation.preparation_ref,
        preparation_digest=preparation.preparation_digest,
    )
    return runtime, provider, preparation, request


def test_fixture_inspects_product_and_checkout_and_recovers_preparation() -> None:
    runtime, _, preparation = _prepare()

    product = runtime.inspect(
        InspectRequest(kind="product", product_ref="product-1")
    )
    checkout = runtime.inspect(
        InspectRequest(kind="checkout", checkout_ref=preparation.checkout_ref)
    )

    assert product.kind == "product"
    assert checkout.kind == "checkout"
    assert preparation.recurring is False
    assert preparation.fulfillment.kind == "shipping"
    assert runtime.recover_preparation(
        PreparationRecoveryLocator(idempotency_key="prepare-1")
    ) == preparation


def test_prepare_order_requires_a_non_empty_item_list() -> None:
    with pytest.raises(ValidationError):
        PrepareOrderRequest(idempotency_key="prepare-empty", items=())


@pytest.mark.parametrize(
    ("provider_state", "order_state", "payment_state"),
    [
        ("succeeded", "accepted", "authorized"),
        ("declined", "declined", "declined"),
        ("action_required", "action_required", "pending"),
    ],
)
def test_fixture_places_accepted_declined_and_authorization_outcomes(
    provider_state: str,
    order_state: str,
    payment_state: str,
) -> None:
    runtime, provider, _, request = _place()
    provider.set_next_placement_state(provider_state)  # type: ignore[arg-type]

    result = runtime.place_order(request)

    assert result.state == provider_state
    assert result.lifecycle.order == order_state
    assert result.lifecycle.payment == payment_state


def test_fixture_rejects_stale_preparation_and_splits_shipments() -> None:
    runtime, provider, preparation, request = _place(quantity=2)
    provider.advance_checkout_revision()
    with pytest.raises(CommerceProviderError, match="stale") as exc_info:
        runtime.place_order(request)
    assert exc_info.value.code == "STALE_REVISION"

    provider.checkout_revision = preparation.checkout_revision
    placement = runtime.place_order(request)
    assert len(placement.lifecycle.shipments) == 2


@pytest.mark.parametrize(
    "kind",
    ["cancel", "partial_cancel", "return", "partial_return", "refund_request"],
)
def test_fixture_prepares_and_applies_closed_order_actions(kind: str) -> None:
    runtime, _, _, place_request = _place()
    placement = runtime.place_order(place_request)
    assert placement.order_ref is not None
    partial = kind in {"partial_cancel", "partial_return"}
    action = runtime.prepare_action(
        PrepareOrderActionRequest(
            idempotency_key=f"prepare-{kind}",
            order_ref=placement.order_ref,
            order_revision=str(placement.order_revision),
            kind=kind,  # type: ignore[arg-type]
            line_item_ids=("line-1",) if partial else (),
            quantity=1 if partial else None,
            reason=(
                "Fixture request"
                if kind in {"return", "partial_return", "refund_request"}
                else None
            ),
            refund_method=(
                "original_payment_method" if kind == "refund_request" else None
            ),
        )
    )
    result = runtime.apply_action(
        ApplyOrderActionRequest(
            idempotency_key=f"apply-{kind}",
            action_ref=action.action_ref,
            order_ref=placement.order_ref,
            action_digest=action.action_digest,
        )
    )

    assert action.eligible is True
    assert result.state == "completed"


def test_fixture_recovers_dropped_placement_and_action_responses() -> None:
    runtime, provider, preparation, place_request = _place()
    provider.drop_next_response("place_order")
    with pytest.raises(DroppedCommerceResponse):
        provider.place_order(place_request)
    placement = runtime.recover_placement(
        PlacementRecoveryLocator(
            idempotency_key=place_request.idempotency_key,
            preparation_digest=preparation.preparation_digest,
        )
    )
    assert placement is not None
    assert placement.order_ref is not None
    assert provider.ledger[-1].operation == "place_order"

    action = runtime.prepare_action(
        PrepareOrderActionRequest(
            idempotency_key="prepare-cancel",
            order_ref=placement.order_ref,
            order_revision=str(placement.order_revision),
            kind="cancel",
        )
    )
    apply_request = ApplyOrderActionRequest(
        idempotency_key="apply-cancel",
        action_ref=action.action_ref,
        order_ref=placement.order_ref,
        action_digest=action.action_digest,
    )
    provider.drop_next_response("apply_action")
    with pytest.raises(DroppedCommerceResponse):
        provider.apply_action(apply_request)
    recovered = runtime.recover_action(
        ActionRecoveryLocator(
            idempotency_key=apply_request.idempotency_key,
            action_digest=action.action_digest,
        )
    )
    assert recovered is not None
    assert recovered.state == "completed"
    assert provider.ledger[-1].operation == "apply_action"


def test_runtime_recovers_dropped_preparation_response() -> None:
    runtime, provider = build_fixture_commerce_runtime()
    provider.drop_next_response("prepare_order")

    preparation = runtime.prepare_order(
        PrepareOrderRequest(
            idempotency_key="prepare-dropped",
            items=(
                RequestedItem(
                    offer_id="offer-1",
                    variant_id="standard",
                    quantity=1,
                ),
            ),
        )
    )

    assert preparation.state == "prepared"
    assert provider.ledger[-1].operation == "prepare_order"
