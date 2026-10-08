from typing import Literal

COMMERCE_SCHEMA_VERSION: Literal["commerce-v1"] = "commerce-v1"

InspectionKind = Literal["product", "checkout", "order", "shipment", "order_actions"]
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

__all__ = [
    "COMMERCE_SCHEMA_VERSION",
    "InspectionKind",
    "OrderActionKind",
    "OrderActionResultState",
    "PlacementState",
    "RefundMethod",
]
