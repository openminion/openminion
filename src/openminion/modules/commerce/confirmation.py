from __future__ import annotations

import re
from typing import Annotated, Any, Literal
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from .models import CommerceDigest


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
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


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
        character
        for character in raw
        if not unicodedata.category(character).startswith("C")
    )
    text = " ".join(clean.split())
    text = text.replace("[", r"\[")
    return text if len(text) <= limit else f"{text[: limit - 3].rstrip()}..."


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
            f"Refund method: {preview.refund_method or '-'}",
            f"Refund destination: {_display(preview.refund_destination_label) if preview.refund_destination_label else '-'}",
            f"Refund destination digest: {preview.refund_destination_digest or '-'}",
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


__all__ = [
    "ActionConfirmationItem",
    "CommerceConfirmationPreview",
    "ConfirmationItem",
    "ExactOrderConfirmationPreview",
    "OrderActionConfirmationPreview",
    "PolicyLink",
    "PreparationIntentConfirmationPreview",
    "commerce_confirmation_payload",
    "commerce_confirmation_lines",
    "parse_commerce_confirmation_preview",
]
