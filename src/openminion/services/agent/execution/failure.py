"""Repeated-failure tracking and typed stall metadata."""

from collections import defaultdict
from dataclasses import dataclass, field

from openminion.services.agent.constants import (
    TERMINATION_REASON_REPEATED_FAILURE_STALLED,
)


AR09_VOCABULARY_VALUE: str = TERMINATION_REASON_REPEATED_FAILURE_STALLED


@dataclass
class RepeatedFailureTracker:
    """Per-turn counter of `(tool_name, args_signature, error_code)` triples."""

    threshold: int = 3
    _counts: dict[tuple[str, str, str], int] = field(
        default_factory=lambda: defaultdict(int)
    )

    def record(self, *, tool_name: str, args_signature: str, error_code: str) -> int:
        """Record one failed triple and return its cumulative count."""
        key = (
            str(tool_name or "").strip(),
            str(args_signature or "").strip(),
            str(error_code or "").strip(),
        )
        self._counts[key] += 1
        return self._counts[key]

    def count(self, *, tool_name: str, args_signature: str, error_code: str) -> int:
        key = (
            str(tool_name or "").strip(),
            str(args_signature or "").strip(),
            str(error_code or "").strip(),
        )
        return self._counts.get(key, 0)

    def stalled_triple(self) -> tuple[str, str, str] | None:
        """Return the first triple whose count >= threshold, or None."""
        for key, count in self._counts.items():
            if count >= self.threshold:
                return key
        return None

    def is_stalled(self) -> bool:
        return self.stalled_triple() is not None

    def snapshot(self) -> dict[str, int]:
        """Return a sortable snapshot for telemetry / metadata."""
        return {
            f"{tool}|{args}|{err}": count
            for (tool, args, err), count in sorted(self._counts.items())
        }


def build_repeated_failure_metadata(
    tracker: RepeatedFailureTracker,
) -> dict[str, str]:
    """Return typed metadata for a `repeated_failure_stalled` outcome."""
    triple = tracker.stalled_triple()
    metadata: dict[str, str] = {
        "tool_loop_termination_reason": AR09_VOCABULARY_VALUE,
        "threshold": str(tracker.threshold),
    }
    if triple is not None:
        tool, args, error = triple
        metadata["stalled_tool_name"] = tool
        metadata["stalled_args_signature"] = args
        metadata["stalled_error_code"] = error
    return metadata


__all__ = [
    "AR09_VOCABULARY_VALUE",
    "RepeatedFailureTracker",
    "build_repeated_failure_metadata",
]
