from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class AnomalyScore:
    score: float
    triggered_conditions: tuple[str, ...]


def detect_anomaly(
    *,
    result: Any,  # ActionResult-like
    history: list[Any],  # prior ActionResults for same tool
    tool_name: str,
) -> AnomalyScore:
    del tool_name  # reserved for future per-tool tuning
    conditions: list[tuple[str, float]] = []

    status = getattr(result, "status", None) or ""
    error = getattr(result, "error", None)
    if error is not None or status in ("error", "failed"):
        conditions.append(("error_status", 1.0))

    result_size = _result_size(result)
    prior_sizes = [_result_size(r) for r in history]
    non_empty_prior = [s for s in prior_sizes if s > 0]
    if result_size == 0 and len(non_empty_prior) > 0:
        conditions.append(("empty_unexpected", 0.7))

    if len(history) >= 1:
        last = history[-1]
        last_error = getattr(last, "error", None)
        last_status = getattr(last, "status", None) or ""
        if (last_error is not None or last_status in ("error", "failed")) and (
            error is not None or status in ("error", "failed")
        ):
            conditions.append(("double_failure", 0.9))

    if len(non_empty_prior) >= 2 and result_size > 0:
        mean_size = sum(non_empty_prior) / len(non_empty_prior)
        if mean_size > 0 and (
            result_size > 3 * mean_size or result_size < mean_size / 3
        ):
            conditions.append(("size_deviation", 0.5))

    if not conditions:
        return AnomalyScore(score=0.0, triggered_conditions=())

    max_score = max(w for _, w in conditions)
    return AnomalyScore(
        score=max_score,
        triggered_conditions=tuple(c for c, _ in conditions),
    )


def _result_size(result: Any) -> int:
    summary = getattr(result, "summary", None) or ""
    outputs = getattr(result, "outputs", None)
    size = len(str(summary))
    if outputs:
        size += len(json.dumps(outputs, default=str))
    return size
