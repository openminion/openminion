import re

_FOOTER_PREFIX = "source="
_FOOTER_LINE_PATTERN = re.compile(rf"(?m)^{re.escape(_FOOTER_PREFIX)}[\w.\-]+\s*$")


def with_source_footer(content: str, provider: str) -> str:
    token = (provider or "").strip().lower()
    if not token:
        return content
    footer = f"{_FOOTER_PREFIX}{token}"
    body = content or ""
    if footer in map(str.strip, body.splitlines()):
        return content
    if not body:
        return footer
    if body.endswith("\n"):
        return f"{body}{footer}"
    return f"{body}\n{footer}"


def has_source_footer(content: str) -> bool:
    return bool(content and _FOOTER_LINE_PATTERN.search(content))


__all__ = ["with_source_footer", "has_source_footer"]
