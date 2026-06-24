from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

_REDACTED = "[REDACTED]"
_SENSITIVE_KEY_RE = re.compile(
    r"(api[_-]?key|token|secret|password|authorization)",
    flags=re.IGNORECASE,
)
_OPENAI_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9_-]{16,}\b")
_BEARER_TOKEN_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._-]{10,}\b")
_GENERIC_CREDENTIAL_RE = re.compile(
    r"(?i)\b(api[_-]?key|token|secret|password)\b\s*[:=]\s*([^\s,;]+)"
)


def redact_sensitive_text(text: str) -> tuple[str, int]:
    value = str(text or "")
    if not value:
        return value, 0

    redacted_total = 0
    value, count = _OPENAI_KEY_RE.subn(_REDACTED, value)
    redacted_total += count
    value, count = _BEARER_TOKEN_RE.subn("Bearer " + _REDACTED, value)
    redacted_total += count

    def _replace_credential(match: re.Match[str]) -> str:
        nonlocal redacted_total
        redacted_total += 1
        return f"{match.group(1)}={_REDACTED}"

    value = _GENERIC_CREDENTIAL_RE.sub(_replace_credential, value)
    return value, redacted_total


def redact_mapping(payload: Mapping[str, Any]) -> tuple[dict[str, Any], int]:
    redacted_total = 0
    redacted: dict[str, Any] = {}
    for key, value in payload.items():
        safe_value, count = _redact_value(value, key_hint=str(key))
        redacted_total += count
        redacted[str(key)] = safe_value
    return redacted, redacted_total


def _redact_value(value: Any, *, key_hint: str) -> tuple[Any, int]:
    if isinstance(value, str):
        if _SENSITIVE_KEY_RE.search(key_hint):
            return (_REDACTED, 1) if value else (value, 0)
        return redact_sensitive_text(value)

    if isinstance(value, Mapping):
        safe, count = redact_mapping(value)
        return safe, count

    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        redacted_items: list[Any] = []
        redacted_total = 0
        for item in value:
            safe_item, count = _redact_value(item, key_hint=key_hint)
            redacted_total += count
            redacted_items.append(safe_item)
        return redacted_items, redacted_total

    return value, 0
