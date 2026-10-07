from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from openminion.modules.commerce.models import (
    ActionRequestState,
    CommerceLifecycleState,
    FulfillmentState,
    LineItem,
    MerchantIdentity,
    Money,
    OrderState,
    PaymentState,
    SafeBuyerProfile,
    SafeDestination,
    SafePaymentMethod,
    SellerIdentity,
    ShipmentState,
)

_DIGEST = "sha256:" + "a" * 64


def test_shared_commerce_models_round_trip() -> None:
    item = LineItem(
        product_id="product-1",
        offer_id="offer-1",
        variant_id="blue-medium",
        line_item_id="line-1",
        quantity=2,
        unit_price=Money(currency="USD", amount_minor=1299),
        availability="available",
        returnable=True,
    )
    payload = {
        "merchant": MerchantIdentity(
            provider_id="merchant-1", display_name="Example Merchant"
        ).model_dump(),
        "seller": SellerIdentity(
            provider_id="seller-1", display_name="Example Seller"
        ).model_dump(),
        "item": item.model_dump(),
        "buyer": SafeBuyerProfile(
            profile_digest=_DIGEST, label="Saved buyer"
        ).model_dump(),
        "destination": SafeDestination(
            destination_digest=_DIGEST,
            label="Home ending 42",
            region="US-CA",
        ).model_dump(),
        "payment": SafePaymentMethod(
            payment_destination_digest=_DIGEST,
            label="Visa ending 4242",
            brand="Visa",
            last_four="4242",
        ).model_dump(),
    }

    assert LineItem.model_validate(payload["item"]) == item
    assert payload["buyer"] == {"profile_digest": _DIGEST, "label": "Saved buyer"}
    assert "record_id" not in str(payload)


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (Money, {"currency": "usd", "amount_minor": 1}),
        (Money, {"currency": "USD", "amount_minor": -1}),
        (
            SafePaymentMethod,
            {
                "payment_destination_digest": "not-a-digest",
                "label": "Card",
                "brand": "Visa",
                "last_four": "4242",
            },
        ),
        (
            SafeBuyerProfile,
            {"profile_digest": _DIGEST, "label": "Buyer", "secret": "no"},
        ),
    ],
)
def test_shared_commerce_models_reject_invalid_payloads(
    model: type[object], payload: dict[str, object]
) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(payload)  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "state_type",
    [OrderState, FulfillmentState, ShipmentState, PaymentState, ActionRequestState],
)
def test_every_lifecycle_state_accepts_provider_unknown(state_type: object) -> None:
    assert TypeAdapter(state_type).validate_python("provider_unknown") == (
        "provider_unknown"
    )
    with pytest.raises(ValidationError):
        TypeAdapter(state_type).validate_python("complete-ish")


def test_lifecycle_state_is_closed_and_versioned() -> None:
    state = CommerceLifecycleState(
        order="accepted",
        fulfillment="partial",
        payment="captured",
        shipments={"shipment-1": "in_transit"},
        action_request="pending",
    )

    assert CommerceLifecycleState.model_validate(state.model_dump()) == state
    assert state.schema_version == "commerce-v1"
    with pytest.raises(ValidationError):
        CommerceLifecycleState.model_validate(
            {**state.model_dump(), "schema_version": "commerce-v2"}
        )
    with pytest.raises(ValidationError):
        CommerceLifecycleState.model_validate({**state.model_dump(), "extra": True})
