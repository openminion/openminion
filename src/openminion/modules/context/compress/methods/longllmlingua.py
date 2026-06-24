from dataclasses import dataclass
from typing import Iterable, Sequence

from ...config import LONGLINGUA_DEFAULT_SEGMENT_SIZE
from ..errors import MethodError
from ..schemas import CompressedBlock, CompressionPolicy, InputBlock
from .extractive import ExtractiveCompressor

METHOD_ID = "longllmlingua.v1"
_VERSION = "v1.0"


@dataclass(frozen=True)
class LongLLMLinguaResult:
    blocks: Sequence[CompressedBlock]
    dropped_reason_stats: dict[str, int]
    warnings: Sequence[str]
    method_meta: dict[str, str]

    @property
    def method_id(self) -> str:
        return METHOD_ID


class LongLLMLinguaCompressor:
    """Long-context segment pruning strategy."""

    def __init__(
        self,
        *,
        available: bool = True,
        segment_size: int = LONGLINGUA_DEFAULT_SEGMENT_SIZE,
    ) -> None:
        self._available = available
        self._segment_size = segment_size
        self._extractor = ExtractiveCompressor()

    @property
    def method_id(self) -> str:
        return METHOD_ID

    @property
    def version(self) -> str:
        return _VERSION

    def is_available(self) -> bool:
        return self._available

    def compress(
        self,
        blocks: Iterable[InputBlock],
        policy: CompressionPolicy,
        query: str,
    ) -> LongLLMLinguaResult:
        if not self._available:
            raise MethodError(
                f"{METHOD_ID} is not available; caller must fall back to extractive.v1"
            )

        block_list = sorted(blocks, key=lambda b: b.block_id)
        if not block_list:
            return LongLLMLinguaResult(
                blocks=(),
                dropped_reason_stats={},
                warnings=(),
                method_meta={"method_id": METHOD_ID, "version": _VERSION},
            )

        # Segment blocks and rank by mean retrieval score.
        segments = self._segment(block_list)
        retained_blocks = self._rank_and_prune(segments)

        ext_result = self._extractor.compress(retained_blocks, policy, query)
        return LongLLMLinguaResult(
            blocks=ext_result.blocks,
            dropped_reason_stats=dict(ext_result.dropped_reason_stats),
            warnings=tuple(ext_result.warnings),
            method_meta={
                "method_id": METHOD_ID,
                "version": _VERSION,
                "segments": str(len(segments)),
            },
        )

    def _segment(self, blocks: list[InputBlock]) -> list[list[InputBlock]]:
        segments = []
        for i in range(0, len(blocks), self._segment_size):
            segments.append(blocks[i : i + self._segment_size])
        return segments

    def _rank_and_prune(self, segments: list[list[InputBlock]]) -> list[InputBlock]:
        if not segments:
            return []

        def segment_score(seg: list[InputBlock]) -> float:
            total = sum(float(b.meta.get("retrieval_score", 0.0)) for b in seg)
            return total / len(seg)

        scored = sorted(segments, key=segment_score, reverse=True)
        # Keep top half of segments (minimum 1).
        keep_n = max(1, (len(scored) + 1) // 2)
        kept_segments = scored[:keep_n]
        # Flatten and preserve original order (by block_id).
        all_blocks = [b for seg in kept_segments for b in seg]
        all_blocks.sort(key=lambda b: b.block_id)
        return all_blocks
