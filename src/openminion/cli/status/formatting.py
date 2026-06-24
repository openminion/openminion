from __future__ import annotations

from typing import Any, Mapping

from openminion.modules.brain.diagnostics.status import (
    PhaseStatus,
    format_phase_status_text,
)


DEFAULT_FALLBACK_LABEL = "Working..."


def format_elapsed_time(elapsed_seconds: float) -> str:
    normalized = max(0.0, float(elapsed_seconds))
    total_seconds = int(normalized)
    if total_seconds < 60:
        return f"{total_seconds}s"
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    return f"{minutes}m {seconds}s"


def format_primary_status_text(
    status: PhaseStatus | Mapping[str, Any] | None,
    *,
    fallback_label: str = DEFAULT_FALLBACK_LABEL,
) -> str:
    return " ".join(
        format_phase_status_text(status, fallback_label=fallback_label)
        .replace("\r", "\n")
        .split()
    )


__all__ = [
    "DEFAULT_FALLBACK_LABEL",
    "format_elapsed_time",
    "format_primary_status_text",
]
