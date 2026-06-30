"""Shared parser helpers for OpenMinion config payloads."""

from typing import Any


def mapping_payload(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    return value if isinstance(value, dict) else {}
