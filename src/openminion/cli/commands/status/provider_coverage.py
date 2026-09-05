from __future__ import annotations

from dataclasses import dataclass

from openminion.modules.telemetry.usage import (
    TokenUsageProviderCoveragePayload,
    TokenUsageSummary,
)
from openminion.modules.telemetry.usage.contracts import (
    TOTAL_SOURCE_DERIVED,
    TOTAL_SOURCE_PROVIDER,
)
from openminion.modules.telemetry.usage.token_usage import (
    SURFACE_LLM_CACHE_DIAGNOSTIC,
    SURFACE_LLM_CACHE_READ,
    SURFACE_LLM_CACHE_WRITE,
    SURFACE_LLM_OUTPUT,
    SURFACE_LLM_PROMPT,
    SURFACE_LLM_TOTAL,
)

_LLM_USAGE_SURFACES = frozenset(
    {
        SURFACE_LLM_TOTAL,
        SURFACE_LLM_PROMPT,
        SURFACE_LLM_OUTPUT,
        SURFACE_LLM_CACHE_READ,
        SURFACE_LLM_CACHE_WRITE,
        SURFACE_LLM_CACHE_DIAGNOSTIC,
    }
)


@dataclass(frozen=True)
class ProviderCoverage:
    provider: str
    model: str
    llm_total_records: int = 0
    provider_total_records: int = 0
    derived_total_records: int = 0
    provider_tokens: int = 0
    derived_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def as_payload(self) -> TokenUsageProviderCoveragePayload:
        return {
            "provider": self.provider,
            "model": self.model,
            "llm_total_records": self.llm_total_records,
            "provider_total_records": self.provider_total_records,
            "derived_total_records": self.derived_total_records,
            "provider_tokens": self.provider_tokens,
            "derived_tokens": self.derived_tokens,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
        }


def provider_coverage_payload(
    summaries: tuple[TokenUsageSummary, ...],
) -> list[TokenUsageProviderCoveragePayload]:
    grouped: dict[tuple[str, str], ProviderCoverage] = {}
    for summary in summaries:
        for record in summary.records:
            if record.surface not in _LLM_USAGE_SURFACES:
                continue
            key = (record.provider or "-", record.model or "-")
            current = grouped.get(key) or ProviderCoverage(*key)
            provider_total = (
                record.surface == SURFACE_LLM_TOTAL
                and record.total_source == TOTAL_SOURCE_PROVIDER
            )
            derived_total = (
                record.surface == SURFACE_LLM_TOTAL
                and record.total_source == TOTAL_SOURCE_DERIVED
            )
            grouped[key] = ProviderCoverage(
                provider=current.provider,
                model=current.model,
                llm_total_records=current.llm_total_records
                + int(record.surface == SURFACE_LLM_TOTAL),
                provider_total_records=current.provider_total_records
                + int(provider_total),
                derived_total_records=current.derived_total_records
                + int(derived_total),
                provider_tokens=current.provider_tokens
                + (record.total_tokens if provider_total else 0),
                derived_tokens=current.derived_tokens
                + (record.total_tokens if derived_total else 0),
                input_tokens=current.input_tokens + record.input_tokens,
                output_tokens=current.output_tokens + record.output_tokens,
                cache_read_tokens=current.cache_read_tokens + record.cache_read_tokens,
                cache_write_tokens=current.cache_write_tokens
                + record.cache_write_tokens,
            )
    ranked = sorted(
        grouped.values(),
        key=lambda item: (
            item.provider_tokens + item.derived_tokens,
            item.llm_total_records,
        ),
        reverse=True,
    )
    return [item.as_payload() for item in ranked]


def format_provider_coverage(summaries: tuple[TokenUsageSummary, ...]) -> list[str]:
    coverage = provider_coverage_payload(summaries)
    if not coverage:
        return []
    parts = []
    for row in coverage[:5]:
        label = f"{row['provider']}/{row['model']}"
        parts.append(
            f"{label}="
            f"records:{row['llm_total_records']} "
            f"provider:{row['provider_tokens']:,} "
            f"derived:{row['derived_tokens']:,} "
            f"cache_read:{row['cache_read_tokens']:,}"
        )
    return ["provider coverage: " + "; ".join(parts)]


__all__ = ["format_provider_coverage", "provider_coverage_payload"]
