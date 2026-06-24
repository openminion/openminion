import hashlib
import json

from .budget import BudgetPlanner
from .errors import MethodError, ValidationError
from .hashing import compute_compression_hash
from .interfaces import COMPRESS_INTERFACE_VERSION
from .methods.extractive import ExtractiveCompressor
from .policy import PolicyResolver
from .registry import MethodRegistry
from .schemas import (
    CompressedBlock,
    CompressionPolicy,
    CompressionReport,
    CompressionRequest,
    CompressionResult,
    InputBlock,
)
from .storage.store import ExplainPayload, TelemetryStore
from .token_count import count_tokens

_ENGINE_VERSION = "2026-03-01"
_TOKENIZER_ID = "whitespace.v1"
_SCORER_VERSION = "v1.0"
_MODE_RESPOND = "respond"
_MODE_ACT = "act"
_MODE_PLAN = "plan"


def _count_tokens(
    blocks: list[InputBlock] | list[CompressedBlock],
) -> int:
    return sum(count_tokens(b.text) for b in blocks)


def _policy_hash(policy: CompressionPolicy) -> str:
    data = json.dumps(
        {k: v for k, v in policy.__dict__.items()},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(data.encode()).hexdigest()[:16]


def _block_hash(
    blocks: list[InputBlock] | list[CompressedBlock],
) -> str:
    data = json.dumps(
        [
            {"id": b.block_id, "text": b.text}
            for b in sorted(blocks, key=lambda x: x.block_id)
        ],
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(data.encode()).hexdigest()[:16]


def _normalized_mode_name(mode_name: str | None) -> str | None:
    normalized = str(mode_name or "").strip().lower()
    if normalized in {_MODE_RESPOND, _MODE_ACT, _MODE_PLAN}:
        return normalized
    return None


def _mode_adjusted_max_units(
    request: CompressionRequest,
    *,
    base_max_units: int,
) -> int:
    normalized_mode = _normalized_mode_name(request.mode_name)
    if normalized_mode == _MODE_RESPOND:
        return max(1, base_max_units - 1)
    if normalized_mode == _MODE_PLAN:
        return base_max_units + 1
    return base_max_units


class CompressionService:
    """Orchestrates the full compression pipeline."""

    contract_version = COMPRESS_INTERFACE_VERSION

    def __init__(
        self,
        *,
        store: TelemetryStore | None = None,
        registry: MethodRegistry | None = None,
    ) -> None:
        self._store = store or TelemetryStore()
        self._registry = registry or MethodRegistry()
        self._resolver = PolicyResolver(self._registry)
        self._budget_planner = BudgetPlanner()

    def compress(
        self,
        request: CompressionRequest,
    ) -> tuple[CompressionResult, str]:
        """Run the compression pipeline. Returns (result, run_id)."""
        self._validate(request)
        resolution = self._resolver.resolve(request)
        block_list = list(request.blocks)

        budget_envelopes = self._budget_planner.plan(request.budgets)

        # Derive max_units from token budget - simple heuristic
        avg_tokens_per_block = max(
            1,
            _count_tokens(block_list) // max(len(block_list), 1),
        )
        max_units = max(1, budget_envelopes.total_cap // avg_tokens_per_block)
        max_units = _mode_adjusted_max_units(request, base_max_units=max_units)

        extractor = ExtractiveCompressor(max_units=max_units)

        warnings: list[str] = list(resolution.warnings)
        fallback_used = resolution.fallback_used
        method_id = resolution.main_method

        try:
            ext_result = extractor.compress(block_list, request.policy, request.query)
            compressed_blocks = list(ext_result.blocks)
            dropped_reason_stats = dict(ext_result.dropped_reason_stats)
            warnings.extend(ext_result.warnings)
        except Exception as exc:
            raise MethodError(f"extractive compressor failed: {exc}") from exc

        # Determine empty augmentation
        empty_aug = (
            len(compressed_blocks) == 0 and request.policy.allow_empty_augmentation
        )
        empty_reason: str | None = None
        if empty_aug:
            if request.retrieval_quality_hint == "BAD":
                empty_reason = "low_quality"
            else:
                empty_reason = "irrelevant"

        input_tokens = _count_tokens(block_list)
        output_tokens = _count_tokens(compressed_blocks)
        ratio = round(output_tokens / max(input_tokens, 1), 6)

        count_by_type: dict[str, int] = {}
        for block in compressed_blocks:
            count_by_type[block.type] = count_by_type.get(block.type, 0) + 1

        report = CompressionReport(
            empty_augmentation=empty_aug,
            empty_reason=empty_reason,
            dropped_reason_stats=dropped_reason_stats,
            count_by_type=count_by_type,
            fallback_used=fallback_used,
            policy_hash=_policy_hash(request.policy),
            input_hash=_block_hash(block_list),
            output_hash=_block_hash(compressed_blocks),
            engine_version=request.engine_version,
            tokenizer_id=_TOKENIZER_ID,
            scorer_version=_SCORER_VERSION,
        )

        # Build result (placeholder hash first, then compute real hash)
        result = CompressionResult(
            blocks=compressed_blocks,
            report=report,
            method_id=method_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            ratio=ratio,
            compression_hash="",
            warnings=warnings,
        )
        compression_hash = compute_compression_hash(request, result)
        # Re-create with real hash (frozen dataclass)
        from dataclasses import replace

        result = replace(result, compression_hash=compression_hash)

        run_id = self._store.record_run(request.request_id, result)
        return result, run_id

    def explain(self, run_id: str) -> ExplainPayload | None:
        """Return the deterministic explain payload for a stored run."""
        return self._store.get_explain_payload(run_id)

    def _validate(self, request: CompressionRequest) -> None:
        if not request.request_id:
            raise ValidationError("request_id is required")
        if not request.query:
            raise ValidationError("query is required")
        if not request.blocks:
            raise ValidationError("blocks must not be empty")
        if request.budgets.max_output_tokens_total <= 0:
            raise ValidationError("budgets.max_output_tokens_total must be positive")
