from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Sequence

from ...config import RECOMP_EXTRACTIVE_DEFAULT_TOP_N
from ..schemas import CompressedBlock, CompressionPolicy, InputBlock
from ..scoring import ScoredUnit, build_scored_units

METHOD_ID = "recomp_extractive.v1"
_VERSION = "v1.0"

# Score floor below which empty augmentation is triggered.
_QUALITY_FLOOR = 0.1


@dataclass(frozen=True)
class RecompResult:
    blocks: Sequence[CompressedBlock]
    dropped_reason_stats: dict[str, int]
    warnings: Sequence[str]
    empty_augmentation: bool
    empty_reason: str | None

    @property
    def method_id(self) -> str:
        return METHOD_ID


class RecompExtractiveCompressor:
    """Retrieval-focused top-N extractive condensation."""

    def __init__(
        self,
        *,
        top_n: int = RECOMP_EXTRACTIVE_DEFAULT_TOP_N,
        quality_floor: float = _QUALITY_FLOOR,
    ) -> None:
        self._top_n = top_n
        self._quality_floor = quality_floor

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
        policy: CompressionPolicy,
        query: str,
        *,
        retrieval_quality_hint: str | None = None,
    ) -> RecompResult:
        block_list = list(blocks)
        scored = build_scored_units(block_list, query)

        # Check quality floor.
        best_score = scored[0].score if scored else 0.0
        low_quality = best_score < self._quality_floor

        if low_quality and policy.allow_empty_augmentation:
            return RecompResult(
                blocks=(),
                dropped_reason_stats={"low_score": len(scored)},
                warnings=(),
                empty_augmentation=True,
                empty_reason="low_quality",
            )

        selected, dropped_stats = self._select_top_n(scored)
        compressed = self._reconstruct(selected)

        if not compressed and policy.allow_empty_augmentation:
            reason = "low_quality" if retrieval_quality_hint == "BAD" else "irrelevant"
            return RecompResult(
                blocks=(),
                dropped_reason_stats=dict(dropped_stats),
                warnings=(),
                empty_augmentation=True,
                empty_reason=reason,
            )

        return RecompResult(
            blocks=compressed,
            dropped_reason_stats=dict(dropped_stats),
            warnings=(),
            empty_augmentation=False,
            empty_reason=None,
        )

    def _select_top_n(
        self,
        scored: list[ScoredUnit],
    ) -> tuple[list[ScoredUnit], dict[str, int]]:
        per_source: dict[str, int] = defaultdict(int)
        selected: list[ScoredUnit] = []
        dropped: dict[str, int] = defaultdict(int)

        for unit in scored:
            if per_source[unit.source_id] < self._top_n:
                selected.append(unit)
                per_source[unit.source_id] += 1
            else:
                dropped["source_cap"] += 1

        return selected, dropped

    def _reconstruct(self, units: list[ScoredUnit]) -> list[CompressedBlock]:
        grouped: dict[str, list[ScoredUnit]] = defaultdict(list)
        for unit in units:
            grouped[unit.block_id].append(unit)

        compressed: list[CompressedBlock] = []
        for block_id in sorted(grouped.keys()):
            group = grouped[block_id]
            text = "\n".join(u.text for u in group)
            refs = list(group[0].refs) if group else []
            compressed.append(
                CompressedBlock(
                    block_id=block_id,
                    type=group[0].block_type,
                    text=text,
                    refs=refs,
                    unit_refs=[f"{u.block_id}:{u.unit_offset}" for u in group],
                    compression_meta={
                        "method_id": METHOD_ID,
                        "top_n": self._top_n,
                        "selected_units": len(group),
                    },
                )
            )
        return compressed
