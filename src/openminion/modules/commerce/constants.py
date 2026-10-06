COMMERCE_SCHEMA_VERSION = "commerce-v1"
COMMERCE_LOCAL_SUBJECT_ID = "local"
PROVIDER_UNKNOWN_STATE = "provider_unknown"

ORDER_STATE_VALUES = (
    "prepared",
    "submitted",
    "accepted",
    "cancel_pending",
    "cancelled",
    "completed",
    "declined",
    "action_required",
    "outcome_unknown",
    PROVIDER_UNKNOWN_STATE,
)
FULFILLMENT_STATE_VALUES = (
    "unfulfilled",
    "partial",
    "fulfilled",
    "cancelled",
    "returned",
    PROVIDER_UNKNOWN_STATE,
)
SHIPMENT_STATE_VALUES = (
    "label_created",
    "in_transit",
    "delayed",
    "exception",
    "out_for_delivery",
    "delivered",
    "return_in_transit",
    "returned",
    "lost",
    PROVIDER_UNKNOWN_STATE,
)
PAYMENT_STATE_VALUES = (
    "pending",
    "authorized",
    "captured",
    "declined",
    "refund_pending",
    "partially_refunded",
    "refunded",
    PROVIDER_UNKNOWN_STATE,
)
ACTION_REQUEST_STATE_VALUES = (
    "prepared",
    "submitting",
    "pending",
    "completed",
    "rejected",
    "failed",
    "outcome_unknown",
    PROVIDER_UNKNOWN_STATE,
)
AVAILABILITY_STATE_VALUES = (
    "available",
    "unavailable",
    "limited",
    PROVIDER_UNKNOWN_STATE,
)

__all__ = [
    "ACTION_REQUEST_STATE_VALUES",
    "AVAILABILITY_STATE_VALUES",
    "COMMERCE_LOCAL_SUBJECT_ID",
    "COMMERCE_SCHEMA_VERSION",
    "FULFILLMENT_STATE_VALUES",
    "ORDER_STATE_VALUES",
    "PAYMENT_STATE_VALUES",
    "PROVIDER_UNKNOWN_STATE",
    "SHIPMENT_STATE_VALUES",
]
