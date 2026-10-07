"""Typed post-purchase commerce action contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .contracts import OrderActionKind, OrderActionResultState, RefundMethod
from .models import (
    CommerceDigest,
    CommerceLifecycleState,
    CommerceModel,
    Money,
    SafeDestination,
)


class PrepareOrderActionRequest(CommerceModel):
    idempotency_key: str = Field(min_length=1)
    order_ref: str = Field(min_length=1)
    order_revision: str = Field(min_length=1)
    kind: OrderActionKind
    line_item_ids: tuple[str, ...] = ()
    quantity: int | None = Field(default=None, ge=1)
    reason: str | None = Field(default=None, min_length=1, max_length=120)
    refund_method: RefundMethod | None = None

    @model_validator(mode="after")
    def validate_action_details(self) -> "PrepareOrderActionRequest":
        if self.kind in {"partial_cancel", "partial_return"} and (
            not self.line_item_ids or self.quantity is None
        ):
            raise ValueError("partial actions require line_item_ids and quantity")
        if (
            self.kind in {"return", "partial_return", "refund_request"}
            and not self.reason
        ):
            raise ValueError("return and refund actions require a reason")
        if self.kind == "refund_request" and self.refund_method is None:
            raise ValueError("refund requests require a refund method")
        return self


class RefundDestination(CommerceModel):
    destination_digest: CommerceDigest
    label: str = Field(min_length=1)


class OrderActionItem(CommerceModel):
    line_item_id: str = Field(min_length=1)
    quantity: int = Field(ge=1)


class OrderActionPreparation(CommerceModel):
    action_ref: str = Field(min_length=1)
    action_revision: str = Field(min_length=1)
    order_ref: str = Field(min_length=1)
    order_revision: str = Field(min_length=1)
    kind: OrderActionKind
    affected_items: tuple[OrderActionItem, ...] = ()
    reason: str | None = Field(default=None, min_length=1, max_length=120)
    eligible: bool
    consequence: str = Field(min_length=1)
    fees: Money
    refund: Money
    refund_method: RefundMethod | None = None
    refund_destination: RefundDestination | None = None
    returnable_line_item_ids: tuple[str, ...] = ()
    return_destination: SafeDestination | None = None
    return_method: Literal["mail", "drop_off", "pickup"] | None = None
    shipment_responsibility: Literal["merchant", "buyer"] | None = None
    deadlines: tuple[str, ...] = ()
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
