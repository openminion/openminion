from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from .constants import (
    ACTION_REQUEST_STATE_VALUES,
    AVAILABILITY_STATE_VALUES,
    COMMERCE_SCHEMA_VERSION,
    FULFILLMENT_STATE_VALUES,
    ORDER_STATE_VALUES,
    PAYMENT_STATE_VALUES,
    SHIPMENT_STATE_VALUES,
)

CommerceDigest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
CommerceHandoffReason = Literal[
    "authentication_required",
    "unsupported_order",
    "unsupported_action",
    "ambiguous_refund",
    "return_label_required",
    "merchant_support_required",
    "delivery_exception",
    "provider_unknown",
]
OrderState = Literal[*ORDER_STATE_VALUES]
FulfillmentState = Literal[*FULFILLMENT_STATE_VALUES]
ShipmentState = Literal[*SHIPMENT_STATE_VALUES]
PaymentState = Literal[*PAYMENT_STATE_VALUES]
ActionRequestState = Literal[*ACTION_REQUEST_STATE_VALUES]
AvailabilityState = Literal[*AVAILABILITY_STATE_VALUES]


class CommerceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Money(CommerceModel):
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    amount_minor: int = Field(ge=0)


class MerchantIdentity(CommerceModel):
    provider_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)


class SellerIdentity(CommerceModel):
    provider_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)


class LineItem(CommerceModel):
    product_id: str = Field(min_length=1)
    offer_id: str = Field(min_length=1)
    variant_id: str = Field(min_length=1)
    line_item_id: str | None = Field(default=None, min_length=1)
    quantity: int = Field(ge=1)
    unit_price: Money
    availability: AvailabilityState
    returnable: bool


class SafeBuyerProfile(CommerceModel):
    profile_digest: CommerceDigest
    label: str = Field(min_length=1)


class SafeDestination(CommerceModel):
    destination_digest: CommerceDigest
    label: str = Field(min_length=1)
    region: str = Field(min_length=1)


class SafePaymentMethod(CommerceModel):
    payment_destination_digest: CommerceDigest
    label: str = Field(min_length=1)
    brand: str = Field(min_length=1)
    last_four: str = Field(pattern=r"^[0-9]{4}$")


class CommerceLifecycleState(CommerceModel):
    schema_version: Literal["commerce-v1"] = COMMERCE_SCHEMA_VERSION
    order: OrderState
    fulfillment: FulfillmentState
    payment: PaymentState
    shipments: dict[str, ShipmentState] = Field(default_factory=dict)
    action_request: ActionRequestState | None = None


class CommerceHandoff(CommerceModel):
    state: Literal["handoff_required"] = "handoff_required"
    commerce_code: Literal["HANDOFF_REQUIRED"] = "HANDOFF_REQUIRED"
    requires_user_takeover: Literal[True] = True
    reason_code: CommerceHandoffReason
    message: str = Field(min_length=1)
    url: str | None = Field(default=None, min_length=1)
    preparation_invalidated: Literal[True] = True
    requires_fresh_inspection: Literal[True] = True
    requires_new_approval: Literal[True] = True


__all__ = [
    "ActionRequestState",
    "AvailabilityState",
    "COMMERCE_SCHEMA_VERSION",
    "CommerceDigest",
    "CommerceHandoff",
    "CommerceHandoffReason",
    "CommerceLifecycleState",
    "CommerceModel",
    "FulfillmentState",
    "LineItem",
    "MerchantIdentity",
    "Money",
    "OrderState",
    "PaymentState",
    "SafeBuyerProfile",
    "SafeDestination",
    "SafePaymentMethod",
    "SellerIdentity",
    "ShipmentState",
]
