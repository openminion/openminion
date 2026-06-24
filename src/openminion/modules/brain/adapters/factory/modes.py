"""Modules brain adapters factory mode selection."""

from typing import Any


def mode_is_local(mode: str) -> bool:
    return str(mode or "").strip().lower() == "local"


def mode_is_strict(mode: str) -> bool:
    return str(mode or "").strip().lower() == "strict"


def raise_if_strict(mode: str) -> None:
    if mode_is_strict(mode):
        raise


def use_local_when_service_missing(mode: str, service: Any) -> bool:
    return mode_is_local(mode) or service is None


__all__ = [
    "mode_is_local",
    "mode_is_strict",
    "raise_if_strict",
    "use_local_when_service_missing",
]
