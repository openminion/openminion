"""Tool runtime text redaction."""

import re

from ..constants import (
    TOOL_REDACTION_MODE_OFF,
    TOOL_REDACTION_MODE_STRICT,
)


__all__ = ["redact_text"]


_PATTERNS_NORMAL = (
    r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*([^\s]+)",
    r"(?i)bearer\s+[a-z0-9\.\-_]+",
)
_PATTERNS_STRICT = _PATTERNS_NORMAL + (r"\b[a-zA-Z0-9_-]{24,}\b",)


def redact_text(text: str, mode: str) -> str:
    if mode == TOOL_REDACTION_MODE_OFF:
        return text
    redacted = text
    patterns = (
        _PATTERNS_STRICT if mode == TOOL_REDACTION_MODE_STRICT else _PATTERNS_NORMAL
    )
    for expr in patterns:
        redacted = re.sub(expr, "[REDACTED]", redacted)
    return redacted
