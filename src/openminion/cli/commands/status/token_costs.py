from __future__ import annotations

from openminion.modules.telemetry.usage import TokenUsageSummary
from openminion.modules.telemetry.usage.contracts import TokenUsageCostTotalsPayload


def format_optional_cost_usd(value: float | None) -> str:
    if value is None:
        return "-"
    return f"${float(value):.6f}".rstrip("0").rstrip(".")


def format_cost_totals(
    provider_cost: float | None, estimated_cost: float | None
) -> str:
    if provider_cost is None and estimated_cost is None:
        return "cost=unavailable"
    return (
        f"provider_cost={format_optional_cost_usd(provider_cost)} "
        f"estimated_cost={format_optional_cost_usd(estimated_cost)}"
    )


def token_cost_rollup(
    summaries: tuple[TokenUsageSummary, ...],
) -> TokenUsageCostTotalsPayload:
    has_provider_cost = any(summary.has_provider_cost for summary in summaries)
    has_estimated_cost = any(summary.has_estimated_cost for summary in summaries)
    return {
        "provider_cost_usd": (
            round(sum(summary.total_provider_cost_usd for summary in summaries), 12)
            if has_provider_cost
            else None
        ),
        "estimated_cost_usd": (
            round(sum(summary.total_estimated_cost_usd for summary in summaries), 12)
            if has_estimated_cost
            else None
        ),
    }


__all__ = ["format_cost_totals", "format_optional_cost_usd", "token_cost_rollup"]
