"""Decision-aware memory surfacing helpers."""

from typing import Any


def annotate_with_recall_source(rendered_line: str, decision: Any) -> str:
    """Prefix a rendered line with the decision source."""

    source = str(getattr(decision, "source", None) or "context").strip()
    if not source:
        source = "context"
    return f"[recall={source}] {rendered_line}"


def render_with_decision(record: Any, decision: Any) -> str:
    """Compose the confidence renderer with the recall-source prefix."""

    from openminion.modules.memory.surfacing.render import (
        render_record_with_confidence,
    )

    return annotate_with_recall_source(render_record_with_confidence(record), decision)


__all__ = ("annotate_with_recall_source", "render_with_decision")
