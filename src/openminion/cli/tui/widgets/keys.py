from __future__ import annotations

from typing import Any


def is_bare_space_key(event: Any) -> bool:
    key = str(getattr(event, "key", "") or "")
    character = getattr(event, "character", None)
    return key in {"space", " "} and character in (None, "")


def is_space_key(event: Any) -> bool:
    key = str(getattr(event, "key", "") or "")
    return key in {"space", " "} or getattr(event, "character", None) == " "


def stop_key_event(event: Any) -> None:
    for method_name in ("stop", "prevent_default"):
        method = getattr(event, method_name, None)
        if callable(method):
            method()
