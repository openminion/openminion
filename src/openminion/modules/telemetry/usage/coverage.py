"""Coverage and correlation quality for token usage source facts."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from .contracts import (
    TokenUsageCoveragePayload,
    TokenUsageDimensionCoveragePayload,
)
from .types import coerce_non_negative_int

INPUT_TOKEN_KEYS = ("input_tokens", "prompt_tokens", "total_input_tokens_used")
OUTPUT_TOKEN_KEYS = (
    "output_tokens",
    "completion_tokens",
    "total_output_tokens_used",
)
CACHE_READ_TOKEN_KEYS = (
    "cache_read_tokens",
    "cached_tokens",
    "cached_input_tokens",
    "usage_cached_tokens",
)
CACHE_WRITE_TOKEN_KEYS = (
    "cache_write_tokens",
    "cache_creation_tokens",
    "cache_creation_input_tokens",
)
TOTAL_TOKEN_KEYS = ("total_tokens", "total_tokens_used")


@dataclass(frozen=True)
class ObservedTokenValue:
    value: int
    state: Literal["reported", "missing", "invalid"]


def observed_token_value(
    payload: Mapping[str, Any],
    keys: tuple[str, ...],
) -> ObservedTokenValue:
    invalid = False
    for key in keys:
        if key not in payload or payload.get(key) is None:
            continue
        value = payload[key]
        if isinstance(value, bool):
            invalid = True
            continue
        try:
            normalized = int(value)
        except (TypeError, ValueError):
            invalid = True
            continue
        if normalized < 0:
            invalid = True
            continue
        return ObservedTokenValue(value=normalized, state="reported")
    return ObservedTokenValue(value=0, state="invalid" if invalid else "missing")


@dataclass(frozen=True)
class TokenUsageDimensionCoverage:
    reported: int = 0
    missing: int = 0
    invalid: int = 0

    def __post_init__(self) -> None:
        for field_name in ("reported", "missing", "invalid"):
            object.__setattr__(
                self,
                field_name,
                coerce_non_negative_int(getattr(self, field_name)),
            )

    @property
    def total(self) -> int:
        return self.reported + self.missing + self.invalid

    def as_payload(self) -> TokenUsageDimensionCoveragePayload:
        return {
            "reported": self.reported,
            "missing": self.missing,
            "invalid": self.invalid,
        }


@dataclass(frozen=True)
class TokenUsageCoverage:
    llm_call_events: int = 0
    context_manifest_events: int = 0
    cache_metric_events: int = 0
    provider_identified_llm_call_events: int = 0
    model_identified_llm_call_events: int = 0
    run_id_present_events: int = 0
    trace_id_present_events: int = 0
    llm_call_id_present_events: int = 0
    input_tokens: TokenUsageDimensionCoverage = field(
        default_factory=TokenUsageDimensionCoverage
    )
    output_tokens: TokenUsageDimensionCoverage = field(
        default_factory=TokenUsageDimensionCoverage
    )
    total_tokens: TokenUsageDimensionCoverage = field(
        default_factory=TokenUsageDimensionCoverage
    )
    cache_read_tokens: TokenUsageDimensionCoverage = field(
        default_factory=TokenUsageDimensionCoverage
    )
    cache_write_tokens: TokenUsageDimensionCoverage = field(
        default_factory=TokenUsageDimensionCoverage
    )

    def __post_init__(self) -> None:
        for field_name in (
            "llm_call_events",
            "context_manifest_events",
            "cache_metric_events",
            "provider_identified_llm_call_events",
            "model_identified_llm_call_events",
            "run_id_present_events",
            "trace_id_present_events",
            "llm_call_id_present_events",
        ):
            object.__setattr__(
                self,
                field_name,
                coerce_non_negative_int(getattr(self, field_name)),
            )

    def as_payload(self) -> TokenUsageCoveragePayload:
        return {
            "llm_call_events": self.llm_call_events,
            "context_manifest_events": self.context_manifest_events,
            "cache_metric_events": self.cache_metric_events,
            "provider_identified_llm_call_events": (
                self.provider_identified_llm_call_events
            ),
            "model_identified_llm_call_events": self.model_identified_llm_call_events,
            "run_id_present_events": self.run_id_present_events,
            "trace_id_present_events": self.trace_id_present_events,
            "llm_call_id_present_events": self.llm_call_id_present_events,
            "input_tokens": self.input_tokens.as_payload(),
            "output_tokens": self.output_tokens.as_payload(),
            "total_tokens": self.total_tokens.as_payload(),
            "cache_read_tokens": self.cache_read_tokens.as_payload(),
            "cache_write_tokens": self.cache_write_tokens.as_payload(),
        }


def _coverage_text(payload: Mapping[str, Any], key: str) -> str:
    return str(payload.get(key, "") or "").strip()


def _coverage_payload(event: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = event.get("payload")
    return payload if isinstance(payload, Mapping) else {}


def _coverage_event_text(event: Mapping[str, Any], key: str) -> str:
    return str(event.get(key, "") or "").strip()


def coverage_from_session_events(
    events: list[dict[str, Any]],
) -> TokenUsageCoverage:
    llm_events = [
        event
        for event in events
        if _coverage_event_text(event, "event_type") == "llm.call.completed"
    ]

    def _dimension(keys: tuple[str, ...]) -> TokenUsageDimensionCoverage:
        reported = missing = invalid = 0
        for event in llm_events:
            usage = _coverage_payload(event).get("usage")
            usage_payload = usage if isinstance(usage, Mapping) else {}
            state = observed_token_value(usage_payload, keys).state
            reported += state == "reported"
            missing += state == "missing"
            invalid += state == "invalid"
        return TokenUsageDimensionCoverage(
            reported=reported,
            missing=missing,
            invalid=invalid,
        )

    event_types = [_coverage_event_text(event, "event_type") for event in events]
    payloads = [_coverage_payload(event) for event in events]
    return TokenUsageCoverage(
        llm_call_events=len(llm_events),
        context_manifest_events=event_types.count("context.manifest.created"),
        cache_metric_events=event_types.count("llm.cache.metrics"),
        provider_identified_llm_call_events=sum(
            bool(_coverage_text(_coverage_payload(event), "provider"))
            for event in llm_events
        ),
        model_identified_llm_call_events=sum(
            bool(_coverage_text(_coverage_payload(event), "model"))
            for event in llm_events
        ),
        run_id_present_events=sum(
            bool(_coverage_text(payload, "run_id")) for payload in payloads
        ),
        trace_id_present_events=sum(
            bool(_coverage_event_text(event, "trace_id")) for event in events
        ),
        llm_call_id_present_events=sum(
            bool(_coverage_text(payload, "llm_call_id")) for payload in payloads
        ),
        input_tokens=_dimension(INPUT_TOKEN_KEYS),
        output_tokens=_dimension(OUTPUT_TOKEN_KEYS),
        total_tokens=_dimension(TOTAL_TOKEN_KEYS),
        cache_read_tokens=_dimension(CACHE_READ_TOKEN_KEYS),
        cache_write_tokens=_dimension(CACHE_WRITE_TOKEN_KEYS),
    )
