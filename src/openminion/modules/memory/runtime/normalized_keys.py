import re
from typing import Final

from ..constants import NORMALIZED_KEY_MAX_LENGTH

BOUNDED_CATEGORIES: Final[frozenset[str]] = frozenset(
    {"fact", "user_preference", "task"}
)
_SLUG_SANITIZE_RE: Final[re.Pattern[str]] = re.compile(r"[^a-z0-9_\-\.]")
_COLLAPSE_SEPARATORS_RE: Final[re.Pattern[str]] = re.compile(r"[_\-]+")
_NORMALIZED_KEY_RE: Final[re.Pattern[str]] = re.compile(
    r"^(fact|user_preference|task):[a-z0-9](?:[a-z0-9_\-\.:]{0,126}[a-z0-9])?$"
)
_MAX_KEY_LENGTH: Final[int] = NORMALIZED_KEY_MAX_LENGTH


def normalize_slug(raw: str) -> str:
    """Sanitize a freeform string into a stable slug fragment."""
    lowered = str(raw or "").strip().lower()
    sanitized = _SLUG_SANITIZE_RE.sub("_", lowered)
    collapsed = _COLLAPSE_SEPARATORS_RE.sub("_", sanitized).strip("_.-")
    return collapsed


def build_normalized_key(*, kind: str, slug: str) -> str:
    """Build a canonical normalized key for a candidate."""
    category = str(kind or "").strip().lower()
    slug_norm = normalize_slug(slug)
    if not slug_norm:
        slug_norm = "unspecified"
    if category not in BOUNDED_CATEGORIES:
        category = f"fact:custom"  # noqa: F541 — intentional literal
        kind_marker = _SLUG_SANITIZE_RE.sub("_", str(kind or "").strip().lower())
        kind_marker = _COLLAPSE_SEPARATORS_RE.sub("_", kind_marker).strip("_")
        if kind_marker and kind_marker not in {"fact", "user_preference", "task"}:
            slug_norm = f"{kind_marker}_{slug_norm}" if slug_norm else kind_marker
    key = f"{category}:{slug_norm}"
    if len(key) > _MAX_KEY_LENGTH:
        prefix = f"{category}:"
        budget = _MAX_KEY_LENGTH - len(prefix)
        key = prefix + slug_norm[:budget]
    return key


def is_valid_normalized_key(key: str) -> bool:
    """Validate a normalized key against the bounded/custom vocabulary."""
    candidate = str(key or "").strip()
    if not candidate or len(candidate) > _MAX_KEY_LENGTH:
        return False
    return bool(_NORMALIZED_KEY_RE.match(candidate))


def parse_normalized_key(key: str) -> tuple[str, str] | None:
    """Split a normalized key into (category, slug); None if invalid."""
    if not is_valid_normalized_key(key):
        return None
    category, _, slug = key.partition(":")
    return category, slug


__all__ = [
    "BOUNDED_CATEGORIES",
    "build_normalized_key",
    "is_valid_normalized_key",
    "normalize_slug",
    "parse_normalized_key",
]
