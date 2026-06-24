import re

# OBGE harness pattern: `source=<provider>` on a stable line.
_FOOTER_PREFIX = "source="

# Stable token pattern: provider name is alnum + `_-.` (matches existing
# provider ids like `openmeteo`, `core-http`, `tavily`, `serpapi`).
_FOOTER_LINE_PATTERN = re.compile(rf"(?m)^{re.escape(_FOOTER_PREFIX)}[\w.\-]+\s*$")


def with_source_footer(content: str, provider: str) -> str:
    """Return ``content`` with a ``source=<provider>`` footer appended."""
    token = (provider or "").strip().lower()
    if not token:
        return content
    footer = f"{_FOOTER_PREFIX}{token}"
    body = content or ""
    # Idempotency: if the exact footer line already exists, no-op.
    for line in body.splitlines():
        if line.strip() == footer:
            return content
    if not body:
        return footer
    if body.endswith("\n"):
        return f"{body}{footer}"
    return f"{body}\n{footer}"


def has_source_footer(content: str) -> bool:
    """True iff ``content`` contains any ``source=<provider>`` footer line."""
    if not content:
        return False
    return bool(_FOOTER_LINE_PATTERN.search(content))


__all__ = ["with_source_footer", "has_source_footer"]
