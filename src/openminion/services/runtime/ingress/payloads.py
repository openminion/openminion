import json
from typing import Any, Optional
from collections.abc import Callable, Mapping

_NO_INTENT_CATEGORY = "none"
_INBOUND_META_SKIP = {
    "channel",
    "user",
    "timeout_seconds",
    "inbound_metadata",
    "idempotency_key",
    "deliver",
    "forced_tools",
    "capability_category",
}


def decode_json_if_string(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    raw = value.strip()
    if not raw:
        return value
    if raw[0] not in {"{", "["}:
        return value
    try:
        return json.loads(raw)
    except Exception:
        return value


def parse_inbound_metadata(
    raw: Any,
    *,
    error_factory: Callable[[str], BaseException],
) -> dict[str, str] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise error_factory("`inbound_metadata` must be an object when provided.")
    parsed: dict[str, str] = {}
    for key, value in raw.items():
        normalized_key = str(key or "").strip()
        if not normalized_key:
            continue
        parsed[normalized_key] = str(value or "").strip()
    return parsed


def apply_inbound_overrides(
    *,
    inbound_metadata: dict[str, str] | None,
    payload: Mapping[str, Any],
) -> dict[str, str] | None:
    updated = dict(inbound_metadata or {})
    for key in ("conversation_id", "thread_id", "attach_id"):
        if key not in updated:
            value = payload.get(key)
            if value is None:
                continue
            text = str(value or "").strip()
            if text:
                updated[key] = text
    for key in ("resume", "reset_session"):
        if key in updated:
            continue
        value = payload.get(key)
        if value is None:
            continue
        if isinstance(value, bool):
            updated[key] = "true" if value else "false"
        else:
            text = str(value or "").strip()
            if text:
                updated[key] = text
    return updated or None


def resolve_deliver(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    if isinstance(raw, (int, float)):
        return bool(raw)
    return True


def parse_forced_tools(raw: Any) -> Optional[list[str]]:
    if raw is None:
        return None
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        decoded = decode_json_if_string(text)
        if isinstance(decoded, list):
            items = decoded
        else:
            items = [part.strip() for part in text.split(",") if part.strip()]
        cleaned = [str(item).strip() for item in items if str(item).strip()]
        return cleaned or None
    if isinstance(raw, (list, tuple, set)):
        cleaned = [str(item).strip() for item in raw if str(item).strip()]
        return cleaned or None
    return None


def resolve_capability_category(
    *,
    explicit_category: Any = None,
) -> Optional[str]:
    explicit = None
    if isinstance(explicit_category, str):
        explicit = explicit_category.strip()
    elif explicit_category is not None:
        explicit = str(explicit_category).strip()

    if explicit:
        if explicit and explicit.lower() != _NO_INTENT_CATEGORY:
            return explicit
        return None

    return None


def apply_managed_meta(
    *,
    inbound_metadata: dict[str, str] | None,
    meta: Mapping[str, Any],
) -> dict[str, str] | None:
    updated = dict(inbound_metadata or {})
    for raw_key, raw_value in meta.items():
        key = str(raw_key or "").strip()
        if not key or key in _INBOUND_META_SKIP:
            continue
        updated[key] = str(raw_value or "").strip()
    return updated or None


def mutable_inbound_metadata(
    inbound_metadata: Mapping[str, str] | None,
) -> dict[str, str] | None:
    if inbound_metadata is None:
        return None
    return dict(inbound_metadata)
