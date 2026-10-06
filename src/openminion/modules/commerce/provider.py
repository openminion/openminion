"""Typed provider boundary for commerce inspection and mutations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Literal, Protocol, TypeAlias

from pydantic import Field, model_validator

from .models import (
    CommerceDigest,
    CommerceLifecycleState,
    CommerceModel,
    LineItem,
    MerchantIdentity,
    Money,
    SafeBuyerProfile,
    SafeDestination,
    SafePaymentMethod,
    SellerIdentity,
)

InspectionKind = Literal[
    "product", "checkout", "order", "shipment", "order_actions"
]
PlacementState = Literal[
    "succeeded", "declined", "action_required", "failed", "outcome_unknown"
]
OrderActionKind = Literal[
    "cancel", "partial_cancel", "return", "partial_return", "refund_request"
]
RefundMethod = Literal["original_payment_method", "store_credit"]
OrderActionResultState = Literal[
    "pending", "completed", "rejected", "failed", "outcome_unknown"
]


class CommerceProviderError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class CommerceOutcomeUnknown(CommerceProviderError):
    def __init__(self, message: str) -> None:
        super().__init__("OUTCOME_UNKNOWN", message)


class ProductInspection(CommerceModel):
    kind: Literal["product"] = "product"
    reference: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    merchant: MerchantIdentity
    seller: SellerIdentity
    item: LineItem
    links: dict[str, str] = Field(default_factory=dict)


class CheckoutInspection(CommerceModel):
    kind: Literal["checkout"] = "checkout"
    reference: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    items: tuple[LineItem, ...]
    subtotal: Money
    total: Money
    warnings: tuple[str, ...] = ()
    links: dict[str, str] = Field(default_factory=dict)


class OrderInspection(CommerceModel):
    kind: Literal["order"] = "order"
    reference: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    items: tuple[LineItem, ...]
    lifecycle: CommerceLifecycleState
    total: Money
    links: dict[str, str] = Field(default_factory=dict)


class ShipmentInspection(CommerceModel):
    kind: Literal["shipment"] = "shipment"
    reference: str = Field(min_length=1)
    order_ref: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    state: Literal[
        "label_created",
        "in_transit",
        "delayed",
        "exception",
        "out_for_delivery",
        "delivered",
        "return_in_transit",
        "returned",
        "lost",
        "provider_unknown",
    ]
    line_item_ids: tuple[str, ...]
    links: dict[str, str] = Field(default_factory=dict)


class OrderActionsInspection(CommerceModel):
    kind: Literal["order_actions"] = "order_actions"
    reference: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    actions: tuple[OrderActionKind, ...]


CommerceInspection: TypeAlias = Annotated[
    ProductInspection
    | CheckoutInspection
    | OrderInspection
    | ShipmentInspection
    | OrderActionsInspection,
    Field(discriminator="kind"),
]


class InspectRequest(CommerceModel):
    kind: InspectionKind
    product_ref: str | None = Field(default=None, min_length=1)
    checkout_ref: str | None = Field(default=None, min_length=1)
    order_ref: str | None = Field(default=None, min_length=1)
    shipment_ref: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_reference(self) -> "InspectRequest":
        expected = {
            "product": self.product_ref,
            "checkout": self.checkout_ref,
            "order": self.order_ref,
            "shipment": self.shipment_ref,
            "order_actions": self.order_ref,
        }[self.kind]
        supplied = tuple(
            value
            for value in (
                self.product_ref,
                self.checkout_ref,
                self.order_ref,
                self.shipment_ref,
            )
            if value is not None
        )
        if expected is None or len(supplied) != 1:
            raise ValueError(f"{self.kind} inspection requires its matching reference")
        return self

    @property
    def reference(self) -> str:
        return str(
            self.product_ref
            or self.checkout_ref
            or self.order_ref
            or self.shipment_ref
        )


class FulfillmentSelection(CommerceModel):
    kind: Literal["shipping"] = "shipping"
    label: str = Field(min_length=1)
    destination: SafeDestination | None = None


class CommerceTotals(CommerceModel):
    subtotal: Money
    discount: Money
    tax: Money
    shipping: Money
    fees: Money
    total: Money


class RequestedItem(CommerceModel):
    offer_id: str = Field(min_length=1)
    variant_id: str = Field(min_length=1)
    quantity: int = Field(ge=1)


class PrepareOrderRequest(CommerceModel):
    idempotency_key: str = Field(min_length=1)
    items: tuple[RequestedItem, ...] = Field(min_length=1)
    promotion_code: str | None = Field(default=None, min_length=1)


@dataclass(frozen=True, repr=False)
class ProviderOrderContext:
    merchant_id: str
    provider_secret: str
    buyer_profile_record: dict[str, Any]
    payment_token_record: dict[str, Any]


class OrderPreparation(CommerceModel):
    preparation_ref: str = Field(min_length=1)
    checkout_ref: str = Field(min_length=1)
    checkout_revision: str = Field(min_length=1)
    items: tuple[LineItem, ...]
    merchant: MerchantIdentity
    seller: SellerIdentity
    fulfillment: FulfillmentSelection
    buyer: SafeBuyerProfile
    destination: SafeDestination
    payment: SafePaymentMethod
    totals: CommerceTotals
    recurring: Literal[False] = False
    warnings: tuple[str, ...] = ()
    links: dict[str, str] = Field(default_factory=dict)
    state: Literal["prepared", "provider_unknown"]
    expires_at: str = Field(min_length=1)
    preparation_digest: CommerceDigest


class PreparationRecoveryLocator(CommerceModel):
    idempotency_key: str = Field(min_length=1)


class PlaceOrderRequest(CommerceModel):
    idempotency_key: str = Field(min_length=1)
    preparation_ref: str = Field(min_length=1)
    preparation_digest: CommerceDigest


class OrderPlacement(CommerceModel):
    idempotency_key: str = Field(min_length=1)
    preparation_ref: str = Field(min_length=1)
    state: PlacementState
    order_ref: str | None = Field(default=None, min_length=1)
    order_revision: str | None = Field(default=None, min_length=1)
    lifecycle: CommerceLifecycleState
    warnings: tuple[str, ...] = ()
    links: dict[str, str] = Field(default_factory=dict)


class PlacementRecoveryLocator(CommerceModel):
    idempotency_key: str = Field(min_length=1)
    preparation_digest: CommerceDigest


class PrepareOrderActionRequest(CommerceModel):
    idempotency_key: str = Field(min_length=1)
    order_ref: str = Field(min_length=1)
    order_revision: str = Field(min_length=1)
    kind: OrderActionKind
    line_item_ids: tuple[str, ...] = ()
    quantity: int | None = Field(default=None, ge=1)
    reason: str | None = Field(default=None, min_length=1)
    refund_method: RefundMethod | None = None

    @model_validator(mode="after")
    def validate_action_details(self) -> "PrepareOrderActionRequest":
        if self.kind in {"partial_cancel", "partial_return"} and (
            not self.line_item_ids or self.quantity is None
        ):
            raise ValueError("partial actions require line_item_ids and quantity")
        if self.kind in {"return", "partial_return", "refund_request"} and not self.reason:
            raise ValueError("return and refund actions require a reason")
        if self.kind == "refund_request" and self.refund_method is None:
            raise ValueError("refund requests require a refund method")
        return self


class RefundDestination(CommerceModel):
    destination_digest: CommerceDigest
    label: str = Field(min_length=1)


class OrderActionPreparation(CommerceModel):
    action_ref: str = Field(min_length=1)
    order_ref: str = Field(min_length=1)
    order_revision: str = Field(min_length=1)
    kind: OrderActionKind
    eligible: bool
    consequence: str = Field(min_length=1)
    refund_method: RefundMethod | None = None
    refund_destination: RefundDestination | None = None
    returnable_line_item_ids: tuple[str, ...] = ()
    return_destination: SafeDestination | None = None
    expires_at: str = Field(min_length=1)
    action_digest: CommerceDigest


class ApplyOrderActionRequest(CommerceModel):
    idempotency_key: str = Field(min_length=1)
    action_ref: str = Field(min_length=1)
    order_ref: str = Field(min_length=1)
    action_digest: CommerceDigest


class OrderActionResult(CommerceModel):
    idempotency_key: str = Field(min_length=1)
    action_ref: str = Field(min_length=1)
    order_ref: str = Field(min_length=1)
    state: OrderActionResultState
    lifecycle: CommerceLifecycleState
    warnings: tuple[str, ...] = ()
    links: dict[str, str] = Field(default_factory=dict)


class ActionRecoveryLocator(CommerceModel):
    idempotency_key: str = Field(min_length=1)
    action_digest: CommerceDigest


class CommerceProvider(Protocol):
    provider_id: str

    def inspect(self, request: InspectRequest) -> CommerceInspection: ...

    def prepare_order(
        self, request: PrepareOrderRequest, context: ProviderOrderContext
    ) -> OrderPreparation: ...

    def recover_preparation(
        self, locator: PreparationRecoveryLocator
    ) -> OrderPreparation | None: ...

    def place_order(self, request: PlaceOrderRequest) -> OrderPlacement: ...

    def recover_placement(
        self, locator: PlacementRecoveryLocator
    ) -> OrderPlacement | None: ...

    def prepare_action(
        self, request: PrepareOrderActionRequest
    ) -> OrderActionPreparation: ...

    def apply_action(self, request: ApplyOrderActionRequest) -> OrderActionResult: ...

    def recover_action(
        self, locator: ActionRecoveryLocator
    ) -> OrderActionResult | None: ...


__all__ = [
    "ActionRecoveryLocator",
    "ApplyOrderActionRequest",
    "CheckoutInspection",
    "CommerceInspection",
    "CommerceProvider",
    "CommerceProviderError",
    "CommerceOutcomeUnknown",
    "CommerceTotals",
    "FulfillmentSelection",
    "InspectRequest",
    "OrderActionKind",
    "OrderActionPreparation",
    "OrderActionResult",
    "OrderActionsInspection",
    "OrderInspection",
    "OrderPlacement",
    "OrderPreparation",
    "PlaceOrderRequest",
    "PlacementRecoveryLocator",
    "PrepareOrderActionRequest",
    "PrepareOrderRequest",
    "PreparationRecoveryLocator",
    "ProductInspection",
    "ProviderOrderContext",
    "RefundDestination",
    "RequestedItem",
    "ShipmentInspection",
]
