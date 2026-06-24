from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Sequence

from ...config import SELECTIVE_CONTEXT_DUPLICATE_THRESHOLD
from ..schemas import InputBlock

METHOD_ID = "selective_context"
_VERSION = "v1.0"


def _normalize(text: str) -> set[str]:
    return {t.lower() for t in text.split() if t.strip()}


@dataclass(frozen=True)
class SelectiveContextResult:
    blocks: Sequence[InputBlock]
    dropped_reason_stats: dict[str, int]
    warnings: Sequence[str]

    @property
    def method_id(self) -> str:
        return METHOD_ID

    @property
    def version(self) -> str:
        return _VERSION


class SelectiveContextPrepass:
    """Deterministic deduplication prepass."""

    def __init__(
        self,
        *,
        threshold: float = SELECTIVE_CONTEXT_DUPLICATE_THRESHOLD,
    ) -> None:
        self._threshold = threshold

    @property
    def method_id(self) -> str:
        return METHOD_ID

    @property
    def version(self) -> str:
        return _VERSION

    def is_available(self) -> bool:
        return True

    def compress(
        self,
        blocks: Iterable[InputBlock],
    ) -> SelectiveContextResult:
        sorted_blocks = sorted(blocks, key=lambda b: b.block_id)
        retained: list[InputBlock] = []
        dropped_stats: dict[str, int] = defaultdict(int)
        seen_signatures: list[set[str]] = []

        for block in sorted_blocks:
            sig = _normalize(block.text)
            if not sig:
                # Empty text — retain as-is (no evidence to compare).
                retained.append(block)
                seen_signatures.append(sig)
                continue

            is_dup = False
            for existing in seen_signatures:
                if not existing:
                    continue
                intersection = len(sig & existing)
                union = len(sig | existing)
                jaccard = intersection / union if union > 0 else 0.0
                if jaccard >= self._threshold:
                    is_dup = True
                    break

            if is_dup:
                dropped_stats["duplicate"] += 1
            else:
                retained.append(block)
                seen_signatures.append(sig)

        return SelectiveContextResult(
            blocks=tuple(retained),
            dropped_reason_stats=dict(dropped_stats),
            warnings=(),
        )
