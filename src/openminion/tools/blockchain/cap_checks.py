"""Monetary caps shared by the configured and resolved blockchain send paths.

One owner for the two ceilings a write is checked against: the maximum estimated
fee already established by ``max_total_fee_wei`` and the maximum native value
carried by ``max_value_wei``. Each helper returns plain details so the calling
path can render its own error shape; none of them reads or writes chain state.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..constants import FEE_CAP_MESSAGE, VALUE_CAP_MESSAGE

__all__ = [
    "FEE_CAP_MESSAGE",
    "VALUE_CAP_MESSAGE",
    "fee_cap_details",
    "stale_cap_fields",
    "value_cap_details",
]


def fee_cap_details(config: Any, max_total_fee_wei: Any) -> dict[str, Any] | None:
    """Return the refusal details when the fee exceeds the configured cap."""
    if int(max_total_fee_wei) <= int(config.max_total_fee_wei):
        return None
    return {
        "max_total_fee_wei": str(max_total_fee_wei),
        "configured_max_total_fee_wei": str(config.max_total_fee_wei),
    }


def value_cap_details(config: Any, value_wei: Any) -> dict[str, Any] | None:
    """Return the refusal details when the native value exceeds the configured cap.

    An empty ``max_value_wei`` means the cap is unset, which leaves value
    unbounded and preserves the behaviour of configurations written before the
    field existed. The boundary is inclusive: a value equal to the cap is allowed.
    """
    if not config.max_value_wei:
        return None
    if int(value_wei) <= int(config.max_value_wei):
        return None
    return {
        "value_wei": str(value_wei),
        "configured_max_value_wei": str(config.max_value_wei),
    }


def stale_cap_fields(config: Any, transaction: Mapping[str, Any]) -> list[str]:
    """Return the stale-field names for a prepared transaction that now breaks a cap."""
    fields: list[str] = []
    if fee_cap_details(config, transaction["max_total_fee_wei"]) is not None:
        fields.append("fees")
    if value_cap_details(config, transaction["value_wei"]) is not None:
        fields.append("value")
    return fields
