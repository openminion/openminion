"""Typed redacted records persisted by the commerce store."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ..models import (
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
from ..provider import OrderPreparation

AttemptKind = Literal["preparation", "placement", "action"]
AttemptState = Literal[
    "reserved",
    "submitted",
    "succeeded",
    "failed",
    "outcome_unknown",
]
SnapshotTarget = Literal["preparation", "order"]


class PreparationPayload(CommerceModel):
    merchant: MerchantIdentity
    seller: SellerIdentity
    items: tuple[LineItem, ...] = Field(min_length=1)
    buyer: SafeBuyerProfile
    destination: SafeDestination
    payment: SafePaymentMethod
    total: Money


class PreparationRecord(CommerceModel):
    preparation_id: str = Field(min_length=1)
    subject_id: str = Field(min_length=1)
    payload_digest: CommerceDigest
    payload: PreparationPayload
    prepared: OrderPreparation | None = None
    preparation_attempt_id: str | None = Field(default=None, min_length=1)
    created_at: str = Field(min_length=1)
    invalidated_at: str | None = Field(default=None, min_length=1)


class OrderRecord(CommerceModel):
    order_id: str = Field(min_length=1)
    subject_id: str = Field(min_length=1)
    preparation_id: str = Field(min_length=1)
    provider_order_digest: CommerceDigest
    payload_digest: CommerceDigest
    payload: PreparationPayload
    lifecycle: CommerceLifecycleState
    placement_attempt_id: str | None = Field(default=None, min_length=1)
    created_at: str = Field(min_length=1)
    updated_at: str = Field(min_length=1)


class MaterialSnapshot(CommerceModel):
    snapshot_id: str = Field(min_length=1)
    subject_id: str = Field(min_length=1)
    target_type: SnapshotTarget
    target_id: str = Field(min_length=1)
    payload_digest: CommerceDigest
    lifecycle: CommerceLifecycleState
    cursor_digest: CommerceDigest | None = None
    observed_at: str = Field(min_length=1)


class CommerceAttempt(CommerceModel):
    attempt_id: str = Field(min_length=1)
    subject_id: str = Field(min_length=1)
    kind: AttemptKind
    target_id: str = Field(min_length=1)
    operation: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    request_digest: CommerceDigest
    authorization_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    attempt_count: int = Field(ge=0)
    state: AttemptState
    response_digest: CommerceDigest | None = None
    provider_reference_digest: CommerceDigest | None = None
    active: bool
    created_at: str = Field(min_length=1)
    updated_at: str = Field(min_length=1)


class AttemptReservation(CommerceModel):
    created: bool
    attempt: CommerceAttempt


__all__ = [
    "AttemptKind",
    "AttemptReservation",
    "AttemptState",
    "CommerceAttempt",
    "MaterialSnapshot",
    "OrderRecord",
    "PreparationPayload",
    "PreparationRecord",
    "SnapshotTarget",
]
