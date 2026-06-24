import hashlib
import json
from typing import Any

from .schemas import (
    CompressionBudgets,
    CompressionReport,
    CompressionRequest,
    CompressionResult,
    CompressionPolicy,
    CompressedBlock,
)

_JSON_SEPARATORS = (",", ":")


def build_canonical_payload(
    request: CompressionRequest,
    result: CompressionResult,
) -> dict[str, Any]:
    """Return canonical payload for hashing/replay."""

    return {
        "request": {
            "request_id": request.request_id,
            "query": request.query,
            "engine_version": request.engine_version,
            "policy": _policy_summary(request.policy),
            "budgets": _budgets_summary(request.budgets),
        },
        "result": {
            "method_id": result.method_id,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "ratio": round(result.ratio, 6),
            "blocks": _blocks_summary(result.blocks),
            "report": _report_summary(result.report),
            "warnings": sorted(result.warnings),
        },
    }


def compute_compression_hash(
    request: CompressionRequest,
    result: CompressionResult,
) -> str:
    payload = build_canonical_payload(request, result)
    return canonical_hash(payload)


def canonical_hash(payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=_JSON_SEPARATORS)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def diff_payloads(a: Any, b: Any, *, prefix: str = "") -> list[str]:
    """Return list of differing paths between two payloads."""

    diffs: list[str] = []
    if type(a) is not type(b):
        diffs.append(prefix or "<root>")
        return diffs

    if isinstance(a, dict):
        keys = sorted(set(a) | set(b))
        for key in keys:
            new_prefix = f"{prefix}.{key}" if prefix else key
            if key not in a or key not in b:
                diffs.append(new_prefix)
                continue
            diffs.extend(diff_payloads(a[key], b[key], prefix=new_prefix))
        return diffs

    if isinstance(a, list):
        max_len = max(len(a), len(b))
        for idx in range(max_len):
            new_prefix = f"{prefix}[{idx}]"
            if idx >= len(a) or idx >= len(b):
                diffs.append(new_prefix)
                continue
            diffs.extend(diff_payloads(a[idx], b[idx], prefix=new_prefix))
        return diffs

    if a != b:
        diffs.append(prefix or "<root>")
    return diffs


def _policy_summary(policy: CompressionPolicy) -> dict[str, Any]:
    return {
        "mode": policy.mode,
        "target_ratio": policy.target_ratio,
        "min_evidence_items": policy.min_evidence_items,
        "max_items_per_source": policy.max_items_per_source,
        "allow_empty_augmentation": policy.allow_empty_augmentation,
        "faithfulness_level": policy.faithfulness_level,
        "quote_budget_tokens": policy.quote_budget_tokens,
        "preserve_refs": policy.preserve_refs,
        "positioning": policy.positioning,
        "method_prepass": policy.method_prepass or "none",
        "method_main": policy.method_main,
        "fallback_method_id": policy.fallback_method_id,
        "abstractive_enabled": policy.abstractive_enabled,
    }


def _budgets_summary(budgets: CompressionBudgets) -> dict[str, Any]:
    return {
        "max_output_tokens_total": budgets.max_output_tokens_total,
        "max_output_tokens_by_type": {
            key: budgets.max_output_tokens_by_type[key]
            for key in sorted(budgets.max_output_tokens_by_type)
        },
        "reserve_tokens_for_headers": budgets.reserve_tokens_for_headers,
        "hard_cap": budgets.hard_cap,
    }


def _block_summary(block: CompressedBlock) -> dict[str, Any]:
    return {
        "block_id": block.block_id,
        "type": block.type,
        "text": block.text,
        "refs": sorted(block.refs),
        "unit_refs": sorted(block.unit_refs),
        "compression_meta": _sorted_dict(block.compression_meta),
    }


def _blocks_summary(blocks: list[CompressedBlock]) -> list[dict[str, Any]]:
    summaries = [_block_summary(block) for block in blocks]
    summaries.sort(key=lambda block: block["block_id"])
    return summaries


def _report_summary(report: CompressionReport) -> dict[str, Any]:
    return {
        "empty_augmentation": report.empty_augmentation,
        "empty_reason": report.empty_reason,
        "dropped_reason_stats": _sorted_dict(report.dropped_reason_stats),
        "count_by_type": _sorted_dict(report.count_by_type),
        "fallback_used": report.fallback_used,
        "policy_hash": report.policy_hash,
        "input_hash": report.input_hash,
        "output_hash": report.output_hash,
        "engine_version": report.engine_version,
        "tokenizer_id": report.tokenizer_id,
        "scorer_version": report.scorer_version,
    }


def _sorted_dict(data: dict[str, Any]) -> dict[str, Any]:
    return {key: data[key] for key in sorted(data)}
