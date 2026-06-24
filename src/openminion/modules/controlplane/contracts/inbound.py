from dataclasses import replace
from typing import Any
import warnings

from .models import InboundMessage


def canonicalize_inbound_message(inbound: InboundMessage) -> InboundMessage:
    """Canonicalize inbound message helper."""
    metadata = _coerce_dict(inbound.metadata)
    meta = _coerce_dict(inbound.meta)
    used_meta_alias = False
    used_thread_alias = False

    if not metadata and meta:
        used_meta_alias = True
        metadata = dict(meta)
    if not meta and metadata:
        meta = dict(metadata)

    thread_id = _normalize_optional_str(inbound.thread_id)
    thread_key = _normalize_optional_str(inbound.thread_key)

    if not thread_id and thread_key:
        derived = _derive_thread_id_from_thread_key(thread_key)
        if derived:
            used_thread_alias = True
            thread_id = derived
    if not thread_key and thread_id:
        thread_key = _derive_thread_key_from_thread_id(inbound.channel, thread_id)

    if used_meta_alias:
        warnings.warn(
            "InboundMessage.meta alias was used without metadata; "
            "populate metadata directly.",
            DeprecationWarning,
            stacklevel=2,
        )
    if used_thread_alias:
        warnings.warn(
            "InboundMessage.thread_key alias was used without thread_id; "
            "populate thread_id directly.",
            DeprecationWarning,
            stacklevel=2,
        )

    changed = (
        metadata != inbound.metadata
        or meta != inbound.meta
        or thread_id != inbound.thread_id
        or thread_key != inbound.thread_key
    )
    if not changed:
        return inbound
    return replace(
        inbound,
        metadata=metadata,
        meta=meta,
        thread_id=thread_id,
        thread_key=thread_key,
    )


def inbound_metadata(inbound: InboundMessage) -> dict[str, Any]:
    """Canonical metadata accessor with compatibility backfill."""
    normalized = canonicalize_inbound_message(inbound)
    return _coerce_dict(normalized.metadata)


def _coerce_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _normalize_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _derive_thread_id_from_thread_key(thread_key: str) -> str | None:
    normalized = _normalize_optional_str(thread_key)
    if not normalized:
        return None
    if ":" in normalized:
        tail = normalized.rsplit(":", 1)[-1].strip()
        return tail or None
    return normalized


def _derive_thread_key_from_thread_id(channel: str, thread_id: str) -> str:
    if channel == "telegram":
        return f"telegram-topic:{thread_id}"
    return thread_id
