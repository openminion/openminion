from __future__ import annotations

from typing import Annotated, Literal, TypeAlias

from pydantic import Field, model_validator

from .contracts import InspectionKind, OrderActionKind
from .models import (
    CommerceLifecycleState,
    CommerceModel,
    LineItem,
    MerchantIdentity,
    Money,
    SellerIdentity,
)


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
    open_action_ids: tuple[str, ...] = ()
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
    merchant_id: str = Field(min_length=1)
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
            self.product_ref or self.checkout_ref or self.order_ref or self.shipment_ref
        )


__all__ = [
    "CheckoutInspection",
    "CommerceInspection",
    "InspectRequest",
    "OrderActionsInspection",
    "OrderInspection",
    "ProductInspection",
    "ShipmentInspection",
]
