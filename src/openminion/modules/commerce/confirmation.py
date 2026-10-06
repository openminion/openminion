from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Annotated, Any, Literal, TypeAlias
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from .models import CommerceDigest, CommerceHandoff
from .provider import (
    CommerceInspection,
    OrderActionPreparation,
    OrderActionResult,
    OrderInspection,
    OrderPlacement,
    OrderPreparation,
    ShipmentInspection,
)


class CommerceConfirmationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ConfirmationItem(CommerceConfirmationModel):
    offer_id: str = Field(min_length=1)
    variant_id: str = Field(min_length=1)
    quantity: int = Field(ge=1)
    line_total_minor: int | None = Field(default=None, ge=0)
    returnable: bool | None = None
    final_sale: bool | None = None


class ActionConfirmationItem(CommerceConfirmationModel):
    line_item_id: str = Field(min_length=1)
    quantity: int = Field(ge=1)


class PolicyLink(CommerceConfirmationModel):
    kind: Literal["terms", "privacy", "shipping", "returns"]
    url: str = Field(min_length=1)


class PreparationIntentConfirmationPreview(CommerceConfirmationModel):
    schema_version: Literal["commerce-confirmation-v1"] = "commerce-confirmation-v1"
    kind: Literal["preparation_intent"] = "preparation_intent"
    merchant: str = Field(min_length=1)
    items: tuple[ConfirmationItem, ...] = Field(min_length=1)
    promotion_code: str | None = None
    buyer_label: str = Field(min_length=1)
    destination_label: str = Field(min_length=1)
    payment_label: str = Field(min_length=1)
    subject_id: Literal["local"]
    session_id: str = Field(min_length=1)
    expires_at: str = Field(min_length=1)
    consequence: Literal[
        "Create or refresh one merchant checkout without placing an order or capturing payment."
    ]


class ExactOrderConfirmationPreview(CommerceConfirmationModel):
    schema_version: Literal["commerce-confirmation-v1"] = "commerce-confirmation-v1"
    kind: Literal["exact_order"] = "exact_order"
    merchant: str = Field(min_length=1)
    seller: str = Field(min_length=1)
    preparation_ref: str = Field(min_length=1)
    items: tuple[ConfirmationItem, ...] = Field(min_length=1)
    discount_minor: int = Field(ge=0)
    tax_minor: int = Field(ge=0)
    shipping_minor: int = Field(ge=0)
    fees_minor: int = Field(ge=0)
    total_minor: int = Field(ge=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    destination_label: str = Field(min_length=1)
    buyer_profile_digest: CommerceDigest
    destination_digest: CommerceDigest
    payment_label: str = Field(min_length=1)
    payment_destination_digest: CommerceDigest
    recurring: Literal[False]
    warnings: tuple[str, ...] = ()
    policy_links: tuple[PolicyLink, ...] = ()
    checkout_revision: str = Field(min_length=1)
    expires_at: str = Field(min_length=1)
    preparation_digest: CommerceDigest
    subject_id: Literal["local"]
    session_id: str = Field(min_length=1)


class OrderActionConfirmationPreview(CommerceConfirmationModel):
    schema_version: Literal["commerce-confirmation-v1"] = "commerce-confirmation-v1"
    kind: Literal["order_action"] = "order_action"
    order_id: str = Field(min_length=1)
    order_revision: str = Field(min_length=1)
    action_kind: Literal[
        "cancel", "partial_cancel", "return", "partial_return", "refund_request"
    ]
    affected_items: tuple[ActionConfirmationItem, ...] = ()
    fees_minor: int = Field(ge=0)
    refund_minor: int = Field(ge=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    refund_method: Literal["original_payment_method", "store_credit"] | None = None
    refund_destination_digest: CommerceDigest | None = None
    refund_destination_label: str | None = None
    deadlines: tuple[str, ...] = ()
    consequence: str = Field(min_length=1)
    action_revision: str = Field(min_length=1)
    expires_at: str = Field(min_length=1)
    action_digest: CommerceDigest
    subject_id: Literal["local"]
    session_id: str = Field(min_length=1)


CommerceConfirmationPreview = Annotated[
    PreparationIntentConfirmationPreview
    | ExactOrderConfirmationPreview
    | OrderActionConfirmationPreview,
    Field(discriminator="kind"),
]
_PREVIEW_ADAPTER = TypeAdapter(CommerceConfirmationPreview)
_INSPECTION_ADAPTER = TypeAdapter(CommerceInspection)
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_REFUND_METHOD_LABELS = {
    "original_payment_method": "original payment method",
    "store_credit": "store credit",
}


class CommerceOutcomeUnknownNotice(CommerceConfirmationModel):
    commerce_code: Literal["OUTCOME_UNKNOWN"]
    mutation_kind: Literal["prepare", "place", "action", "apply_action"]
    provider_attempts: int = Field(ge=1)
    recovery_required: Literal[True]


CommercePresentationResult: TypeAlias = (
    OrderPreparation
    | OrderPlacement
    | OrderInspection
    | ShipmentInspection
    | OrderActionPreparation
    | OrderActionResult
    | CommerceOutcomeUnknownNotice
    | CommerceHandoff
)


def parse_commerce_confirmation_preview(
    value: CommerceConfirmationPreview | dict[str, Any],
) -> CommerceConfirmationPreview:
    return _PREVIEW_ADAPTER.validate_python(value)


def commerce_confirmation_payload(
    value: CommerceConfirmationPreview | dict[str, Any],
) -> dict[str, Any]:
    return parse_commerce_confirmation_preview(value).model_dump(mode="json")


def _display(value: object, *, limit: int = 240) -> str:
    raw = _ANSI_ESCAPE.sub(" ", str(value or ""))
    clean = "".join(
        " " if unicodedata.category(character).startswith("C") else character
        for character in raw
    )
    text = " ".join(clean.split())
    text = text.replace("[", r"\[")
    return text if len(text) <= limit else f"{text[: limit - 3].rstrip()}..."


def _refund_method(value: str | None) -> str:
    return _REFUND_METHOD_LABELS.get(value or "", "-")


def commerce_confirmation_lines(
    value: CommerceConfirmationPreview | dict[str, Any],
) -> list[str]:
    preview = parse_commerce_confirmation_preview(value)
    if isinstance(preview, PreparationIntentConfirmationPreview):
        lines = [
            "Commerce checkout preparation",
            f"Merchant: {_display(preview.merchant)}",
            *(
                "Item: "
                f"offer {_display(item.offer_id)}, variant {_display(item.variant_id)}, "
                f"quantity {item.quantity}"
                for item in preview.items
            ),
            f"Promotion: {_display(preview.promotion_code) if preview.promotion_code else '-'}",
            f"Buyer: {_display(preview.buyer_label)}",
            f"Destination: {_display(preview.destination_label)}",
            f"Payment: {_display(preview.payment_label)}",
            f"Expires: {_display(preview.expires_at)}",
            f"Effect: {preview.consequence}",
        ]
    elif isinstance(preview, ExactOrderConfirmationPreview):
        lines = [
            "Exact order review",
            f"Merchant: {_display(preview.merchant)}",
            f"Seller: {_display(preview.seller)}",
            *(
                "Item: "
                f"offer {_display(item.offer_id)}, variant {_display(item.variant_id)}, "
                f"quantity {item.quantity}, line total {item.line_total_minor} "
                f"{preview.currency} minor units"
                for item in preview.items
            ),
            f"Discount: {preview.discount_minor} {preview.currency} minor units",
            f"Tax: {preview.tax_minor} {preview.currency} minor units",
            f"Shipping: {preview.shipping_minor} {preview.currency} minor units",
            f"Fees: {preview.fees_minor} {preview.currency} minor units",
            f"Exact total: {preview.total_minor} {preview.currency} minor units",
            f"Destination: {_display(preview.destination_label)}",
            f"Destination digest: {preview.destination_digest}",
            f"Payment: {_display(preview.payment_label)}",
            f"Payment destination digest: {preview.payment_destination_digest}",
            "Recurring: no",
            f"Checkout revision: {_display(preview.checkout_revision)}",
            f"Preparation digest: {preview.preparation_digest}",
            f"Expires: {_display(preview.expires_at)}",
        ]
        lines.extend(f"Warning: {_display(warning)}" for warning in preview.warnings)
        lines.extend(
            f"{link.kind.title()} policy: {_display(link.url)}"
            for link in preview.policy_links
        )
    else:
        refund_destination = (
            _display(preview.refund_destination_label)
            if preview.refund_destination_label
            else "-"
        )
        lines = [
            "Order action review",
            f"Order: {_display(preview.order_id)}",
            f"Order revision: {_display(preview.order_revision)}",
            f"Action: {preview.action_kind}",
            *(
                f"Affected item: {_display(item.line_item_id)}, quantity {item.quantity}"
                for item in preview.affected_items
            ),
            f"Fees: {preview.fees_minor} {preview.currency} minor units",
            f"Refund: {preview.refund_minor} {preview.currency} minor units",
            f"Refund method: {_refund_method(preview.refund_method)}",
            f"Refund destination: {refund_destination}",
            f"Refund destination digest: {preview.refund_destination_digest or '-'}",
            (
                f"Refund consent: {preview.refund_minor} {preview.currency} minor "
                f"units to {_refund_method(preview.refund_method)} at "
                f"{refund_destination}."
            ),
            f"Effect: {_display(preview.consequence)}",
            f"Action revision: {_display(preview.action_revision)}",
            f"Action digest: {preview.action_digest}",
            f"Expires: {_display(preview.expires_at)}",
        ]
        lines.extend(
            f"Deadline: {_display(deadline)}" for deadline in preview.deadlines
        )
    lines.extend(
        [
            f"Subject: {preview.subject_id}",
            f"Session: {_display(preview.session_id)}",
        ]
    )
    return lines


def commerce_result_lines(value: CommercePresentationResult) -> list[str]:
    if isinstance(value, OrderPreparation):
        lines = [
            "Checkout prepared",
            f"Merchant: {_display(value.merchant.display_name)}",
            f"Seller: {_display(value.seller.display_name)}",
            f"Preparation: {_display(value.preparation_ref)}",
            f"Checkout revision: {_display(value.checkout_revision)}",
            *(
                "Item: "
                f"offer {_display(item.offer_id)}, variant {_display(item.variant_id)}, "
                f"quantity {item.quantity}, unit price {item.unit_price.amount_minor} "
                f"{item.unit_price.currency} minor units"
                for item in value.items
            ),
            f"Discount: {value.totals.discount.amount_minor} {value.totals.discount.currency} minor units",
            f"Tax: {value.totals.tax.amount_minor} {value.totals.tax.currency} minor units",
            f"Shipping: {value.totals.shipping.amount_minor} {value.totals.shipping.currency} minor units",
            f"Fees: {value.totals.fees.amount_minor} {value.totals.fees.currency} minor units",
            f"Exact total: {value.totals.total.amount_minor} {value.totals.total.currency} minor units",
            f"Destination: {_display(value.destination.label)}",
            f"Destination digest: {value.destination.destination_digest}",
            f"Payment: {_display(value.payment.label)}",
            f"Payment destination digest: {value.payment.payment_destination_digest}",
            "Recurring: no",
            f"Expires: {_display(value.expires_at)}",
            "Next action: Review the exact order details, then allow placement once or deny.",
        ]
        lines.extend(f"Warning: {_display(item)}" for item in value.warnings)
        return lines
    if isinstance(value, OrderPlacement):
        lines = [
            "Order receipt",
            f"State: {value.state}",
            f"Preparation: {_display(value.preparation_ref)}",
            f"Order: {_display(value.order_ref) if value.order_ref else '-'}",
            f"Order revision: {_display(value.order_revision) if value.order_revision else '-'}",
            f"Fulfillment: {value.lifecycle.fulfillment}",
            f"Payment: {value.lifecycle.payment}",
            _placement_next_action(value),
        ]
        lines.extend(f"Warning: {_display(item)}" for item in value.warnings)
        return lines
    if isinstance(value, OrderInspection):
        return [
            "Order tracking",
            f"Order: {_display(value.reference)}",
            f"Revision: {_display(value.revision)}",
            f"Total: {value.total.amount_minor} {value.total.currency} minor units",
            f"Order state: {value.lifecycle.order}",
            f"Fulfillment: {value.lifecycle.fulfillment}",
            f"Payment: {value.lifecycle.payment}",
            *(
                f"Shipment {_display(shipment_id)}: {state}"
                for shipment_id, state in sorted(value.lifecycle.shipments.items())
            ),
            "Next action: Continue tracking any shipment that is not yet delivered or returned.",
        ]
    if isinstance(value, ShipmentInspection):
        return [
            "Shipment tracking",
            f"Shipment: {_display(value.reference)}",
            f"Order: {_display(value.order_ref)}",
            f"Revision: {_display(value.revision)}",
            f"State: {value.state}",
            *(
                f"Line item: {_display(line_item_id)}"
                for line_item_id in value.line_item_ids
            ),
            _shipment_next_action(value),
        ]
    if isinstance(value, OrderActionPreparation):
        refund_destination = (
            _display(value.refund_destination.label)
            if value.refund_destination is not None
            else "-"
        )
        return [
            "Order care review",
            f"Order: {_display(value.order_ref)}",
            f"Order revision: {_display(value.order_revision)}",
            f"Action: {value.kind}",
            f"Eligible: {'yes' if value.eligible else 'no'}",
            f"Effect: {_display(value.consequence)}",
            f"Refund method: {_refund_method(value.refund_method)}",
            f"Refund destination: {refund_destination}",
            "Refund destination digest: "
            + (
                value.refund_destination.destination_digest
                if value.refund_destination is not None
                else "-"
            ),
            "Return destination: "
            + (
                _display(value.return_destination.label)
                if value.return_destination is not None
                else "-"
            ),
            f"Expires: {_display(value.expires_at)}",
            (
                "Next action: Review these exact care terms, then allow once or deny."
                if value.eligible
                else "Next action: Inspect the order or request merchant support."
            ),
        ]
    if isinstance(value, OrderActionResult):
        lines = [
            "Order care receipt",
            f"Order: {_display(value.order_ref)}",
            f"Action: {_display(value.action_ref)}",
            f"State: {value.state}",
            f"Order state: {value.lifecycle.order}",
            f"Payment: {value.lifecycle.payment}",
            (
                "Next action: Inspect the order before retrying; the action outcome is unknown."
                if value.state == "outcome_unknown"
                else "Next action: Track the updated order state."
            ),
        ]
        lines.extend(f"Warning: {_display(item)}" for item in value.warnings)
        return lines
    if isinstance(value, CommerceOutcomeUnknownNotice):
        return [
            "Commerce outcome unknown",
            f"Operation: {value.mutation_kind}",
            f"Provider attempts: {value.provider_attempts}",
            "Next action: Inspect or recover the operation before retrying; do not submit it again yet.",
        ]
    return [
        "Merchant handoff required",
        f"Reason: {value.reason_code}",
        f"Message: {_display(value.message)}",
        f"Open: {_display(value.url) if value.url else '-'}",
        "Preparation invalidated: yes",
        "Next action: Complete the merchant handoff, inspect again, and request a new approval.",
    ]


def _placement_next_action(value: OrderPlacement) -> str:
    if value.state == "outcome_unknown":
        return "Next action: Inspect or recover this order before retrying; do not place it again yet."
    if value.state == "succeeded" and value.order_ref:
        return f"Next action: Track order {_display(value.order_ref)}."
    if value.state == "action_required":
        return (
            "Next action: Inspect the order and complete the required merchant action."
        )
    return "Next action: Review the receipt before preparing a new checkout."


def _shipment_next_action(value: ShipmentInspection) -> str:
    if value.state in {"delivered", "returned"}:
        return "Next action: No shipment action is required."
    if value.state in {"exception", "lost"}:
        return "Next action: Request merchant care for this shipment."
    return "Next action: Continue tracking this shipment."


def commerce_tool_result_lines(
    tool_name: str,
    value: Mapping[str, Any],
) -> list[str]:
    payload = dict(value)
    if payload.get("commerce_code") == "OUTCOME_UNKNOWN":
        return commerce_result_lines(
            CommerceOutcomeUnknownNotice.model_validate(payload)
        )
    if payload.get("state") == "handoff_required":
        return commerce_result_lines(CommerceHandoff.model_validate(payload))
    normalized_tool = str(tool_name or "").strip()
    if normalized_tool == "commerce.inspect":
        result = _INSPECTION_ADAPTER.validate_python(payload)
    elif normalized_tool == "commerce.prepare_order":
        result = OrderPreparation.model_validate(_without_runtime_facts(payload))
    elif normalized_tool == "commerce.place_order":
        result = OrderPlacement.model_validate(_without_runtime_facts(payload))
    elif normalized_tool == "commerce.prepare_order_action":
        result = OrderActionPreparation.model_validate(_without_runtime_facts(payload))
    elif normalized_tool == "commerce.apply_order_action":
        result = OrderActionResult.model_validate(_without_runtime_facts(payload))
    else:
        raise ValueError(f"Unsupported commerce presentation tool: {normalized_tool}")
    return commerce_result_lines(result)


def _without_runtime_facts(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item
        for key, item in value.items()
        if key not in {"mutation_kind", "provider_attempts", "recovery_required"}
    }


__all__ = [
    "ActionConfirmationItem",
    "CommerceConfirmationPreview",
    "CommerceOutcomeUnknownNotice",
    "CommercePresentationResult",
    "ConfirmationItem",
    "ExactOrderConfirmationPreview",
    "OrderActionConfirmationPreview",
    "PolicyLink",
    "PreparationIntentConfirmationPreview",
    "commerce_confirmation_payload",
    "commerce_confirmation_lines",
    "commerce_result_lines",
    "commerce_tool_result_lines",
    "parse_commerce_confirmation_preview",
]
