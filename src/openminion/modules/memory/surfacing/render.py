"""Confidence-surfaced render helpers."""

from typing import Any


CONFIDENCE_BAND_HIGH = "high"
CONFIDENCE_BAND_MEDIUM = "medium"
CONFIDENCE_BAND_LOW = "low"


def confidence_band(value: float, *, high: float = 0.8, low: float = 0.4) -> str:
    """Bucket a numeric confidence into a closed-set band string."""

    v = float(value)
    if v >= float(high):
        return CONFIDENCE_BAND_HIGH
    if v >= float(low):
        return CONFIDENCE_BAND_MEDIUM
    return CONFIDENCE_BAND_LOW


def render_record_with_confidence(record: Any) -> str:
    """Render one record with the confidence band visible to the model."""

    title = str(getattr(record, "title", "") or "").strip()
    content = getattr(record, "content", "") or ""
    if isinstance(content, dict):
        content = str(content.get("text", content.get("value", str(content))))
    content_text = str(content or "").strip()
    confidence = float(getattr(record, "confidence", 0.0) or 0.0)
    band = confidence_band(confidence)
    body = (
        f"{title}: {content_text}"
        if title and content_text and title.lower() != content_text.lower()
        else (title or content_text[:120])
    )
    return f"[confidence={band}] {body}"


def format_records_with_confidence(
    records: list[Any],
    *,
    header: str = "[recalled memory]",
    max_chars: int = 4000,
) -> str:
    """Render a list of records with confidence bands surfaced inline."""

    if not records:
        return ""
    lines = [header]
    for rec in records:
        lines.append(f"  • {render_record_with_confidence(rec)}")
    out = "\n".join(lines)
    if len(out) > max_chars:
        out = out[:max_chars] + "\n  [truncated]"
    return out


__all__ = (
    "CONFIDENCE_BAND_HIGH",
    "CONFIDENCE_BAND_LOW",
    "CONFIDENCE_BAND_MEDIUM",
    "confidence_band",
    "format_records_with_confidence",
    "render_record_with_confidence",
)
