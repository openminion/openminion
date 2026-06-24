"""Shared support functions for context bridge adapters."""

import logging
from pathlib import Path
from typing import Any, Callable

from openminion.modules.brain.interfaces import BRAIN_ADAPTER_INTERFACE_VERSION

LOGGER_NAME = "openminion.modules.brain.adapters.context.bridges"
_LOGGER = logging.getLogger(LOGGER_NAME)
_IDENTITY_BRIDGE_FALLBACK_VERSION = "brain-bridge:fallback:v1"


def _resolve_database_path(backing_store: Any) -> Path | None:
    base_store = getattr(backing_store, "store", backing_store)
    for attr in ("database_path", "_path", "sqlite_path"):
        value = getattr(base_store, attr, None)
        if not value:
            continue
        try:
            return Path(value).expanduser().resolve()
        except Exception:
            continue
    return None


def _extract_text_from_record(
    record: Any,
    *,
    content_dict_keys: tuple[str, ...] = (
        "text",
        "summary",
        "summary_text",
        "value",
        "note",
        "content",
    ),
    attr_keys: tuple[str, ...] = ("title", "key"),
) -> str:
    content = getattr(record, "content", "")
    if isinstance(content, dict):
        for key in content_dict_keys:
            value = content.get(key)
            if value:
                return str(value)
    if isinstance(content, str) and content.strip():
        return content
    for key in attr_keys:
        value = getattr(record, key, "")
        if value:
            return str(value)
    return ""


def _lazy_resolve_service(
    instance: Any,
    *,
    cache_attr: str,
    import_loader: Callable[[], Any | None],
    factory: Callable[[Any], Any | None],
) -> Any | None:
    cached = getattr(instance, cache_attr, None)
    if cached is not None:
        return cached
    imported = import_loader()
    if imported is None:
        return None
    try:
        service = factory(imported)
    except Exception:
        service = None
    setattr(instance, cache_attr, service)
    return service


def _normalized_string_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return [text for value in values if (text := str(value).strip())]


__all__ = [
    "BRAIN_ADAPTER_INTERFACE_VERSION",
    "LOGGER_NAME",
    "_IDENTITY_BRIDGE_FALLBACK_VERSION",
    "_LOGGER",
    "_extract_text_from_record",
    "_lazy_resolve_service",
    "_normalized_string_list",
    "_resolve_database_path",
]
