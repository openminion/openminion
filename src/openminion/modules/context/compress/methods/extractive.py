from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Sequence

from ..schemas import CompressedBlock, CompressionPolicy, InputBlock
from ..scoring import ScoredUnit, build_scored_units

METHOD_ID = "extractive.v1"


@dataclass(frozen=True)
class ExtractiveResult:
    blocks: Sequence[CompressedBlock]
    dropped_reason_stats: dict[str, int]
    warnings: Sequence[str]


class ExtractiveCompressor:
    """Implements deterministic scoring/selection/reconstruction."""

    def __init__(self, *, max_units: int | None = None) -> None:
        self._max_units = max_units

    @property
    def method_id(self) -> str:
        return METHOD_ID

    def compress(
        self,
        blocks: Iterable[InputBlock],
        policy: CompressionPolicy,
        query: str,
    ) -> ExtractiveResult:
        scored_units = build_scored_units(blocks, query)
        selected, dropped = self._select_units(scored_units, policy)
        compressed_blocks = self._reconstruct(selected)
        return ExtractiveResult(
            blocks=compressed_blocks,
            dropped_reason_stats=dropped,
            warnings=(),
        )

    # Selection ------------------------------------------------------------
    def _select_units(
        self,
        units: Sequence[ScoredUnit],
        policy: CompressionPolicy,
    ) -> tuple[list[ScoredUnit], dict[str, int]]:
        selected: list[ScoredUnit] = []
        dropped_reason_stats: dict[str, int] = defaultdict(int)
        per_source: dict[str, int] = defaultdict(int)
        max_units = self._max_units or len(units)
        for unit in units:
            if len(selected) >= max_units:
                dropped_reason_stats["budget"] += 1
                continue
            if per_source[unit.source_id] >= policy.max_items_per_source:
                dropped_reason_stats["source_cap"] += 1
                continue
            per_source[unit.source_id] += 1
            selected.append(unit)
        return selected, dropped_reason_stats

    # Reconstruction -------------------------------------------------------
    def _reconstruct(self, units: Sequence[ScoredUnit]) -> list[CompressedBlock]:
        grouped: dict[str, list[ScoredUnit]] = defaultdict(list)
        for unit in units:
            grouped[unit.block_id].append(unit)
        compressed: list[CompressedBlock] = []
        for block_id in sorted(grouped.keys()):
            group = grouped[block_id]
            text = "\n".join(unit.text for unit in group)
            refs = group[0].refs if group else []
            compressed.append(
                CompressedBlock(
                    block_id=block_id,
                    type=group[0].block_type,
                    text=text,
                    refs=list(refs),
                    unit_refs=[f"{unit.block_id}:{unit.unit_offset}" for unit in group],
                    compression_meta={
                        "method_id": METHOD_ID,
                        "selected_units": len(group),
                    },
                )
            )
        return compressed
