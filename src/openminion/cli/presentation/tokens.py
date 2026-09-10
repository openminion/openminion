from __future__ import annotations

from collections import defaultdict
import shlex
from typing import Any

from openminion.cli.commands.status.token_costs import format_optional_cost_usd
from openminion.cli.status.token_usage import format_token_count
from openminion.modules.telemetry.usage import TokenUsageSummary
from openminion.modules.telemetry.usage.token_usage import (
    SURFACE_CONTEXT_PACK,
    SURFACE_LLM_TOTAL,
)

DEFAULT_RECENT_SESSIONS = 10
MAX_RECENT_SESSIONS = 20
TOKENS_USAGE = "usage: /tokens [recent [1..20]]"


def render_tokens_slash(args: str, *, runtime: Any) -> str:
    try:
        parts = shlex.split(str(args or ""))
    except ValueError:
        return TOKENS_USAGE
    if not parts:
        return str(runtime.token_usage_report()).strip()
    if parts[0] != "recent" or len(parts) > 2:
        return TOKENS_USAGE
    if len(parts) == 1:
        limit = DEFAULT_RECENT_SESSIONS
    else:
        try:
            limit = int(parts[1])
        except ValueError:
            return TOKENS_USAGE
        if not 1 <= limit <= MAX_RECENT_SESSIONS:
            return TOKENS_USAGE
    return str(runtime.token_usage_report(recent=limit)).strip()


def format_interactive_token_summary(summary: TokenUsageSummary) -> str:
    observed, unmetered, failed = _call_counts(summary)
    if not observed:
        return "\n".join(
            (
                "Token usage",
                "No model calls in this session yet.",
                "Next: send a prompt, then run /tokens.",
                f"History: /tokens recent {DEFAULT_RECENT_SESSIONS}",
            )
        )
    if not summary.records:
        return "\n".join(
            (
                "Token usage",
                f"Session: {_short_label(summary.session_id)}",
                "Tokens: unavailable from the provider",
                (
                    f"Calls: {observed} observed · {unmetered} unmetered · "
                    f"{failed} failed"
                ),
                f"History: /tokens recent {DEFAULT_RECENT_SESSIONS}",
            )
        )
    provider_tokens = summary.total_provider_tokens
    derived_tokens = summary.total_derived_tokens
    total_tokens = provider_tokens + derived_tokens
    context_tokens = summary.totals_by_surface.get(SURFACE_CONTEXT_PACK, 0)
    model, _ = _top_model(summary)
    lines = [
        "Token usage",
        f"Session: {_short_label(summary.session_id)}",
        f"Model: {model or 'unavailable'}",
        (
            f"Tokens: {format_token_count(total_tokens)} total · "
            f"{format_token_count(summary.total_input_tokens)} input · "
            f"{format_token_count(summary.total_output_tokens)} output"
        ),
        f"Context assembled: {format_token_count(context_tokens)} estimated",
        (
            "Cache: "
            f"{format_token_count(summary.total_cache_read_tokens)} read · "
            f"{format_token_count(summary.total_cache_write_tokens)} write"
        ),
        f"Cost: {_cost_label(summary)}",
        (
            f"Calls: {summary.coverage.llm_call_events} metered · "
            f"{summary.coverage.unmetered_llm_call_events} unmetered"
        ),
        f"History: /tokens recent {DEFAULT_RECENT_SESSIONS}",
    ]
    return "\n".join(lines)


def format_interactive_token_history(
    summaries: tuple[TokenUsageSummary, ...],
    *,
    requested: int,
) -> str:
    if not summaries:
        return "\n".join(
            (
                "Token history",
                "No sessions found for this agent.",
                "Next: complete a model turn, then run /tokens recent 10.",
            )
        )
    used = tuple(
        summary for summary in summaries if summary.coverage.observed_llm_call_events
    )
    if not used:
        return "\n".join(
            (
                "Token history",
                f"No model calls in the newest {len(summaries)} sessions.",
                "Next: complete a model turn, then run /tokens recent 10.",
            )
        )
    total = sum(
        summary.total_provider_tokens + summary.total_derived_tokens for summary in used
    )
    context = sum(
        summary.totals_by_surface.get(SURFACE_CONTEXT_PACK, 0) for summary in used
    )
    lines = [
        f"Token history · {len(used)} of {len(summaries)} sessions with model calls",
        (
            f"Totals: {format_token_count(total)} model · "
            f"{format_token_count(context)} context estimated · "
            f"cost {_history_cost_label(used)}"
        ),
        _history_call_coverage(used),
        "Recent sessions:",
    ]
    for index, summary in enumerate(used):
        model, _ = _top_model(summary)
        tokens = summary.total_provider_tokens + summary.total_derived_tokens
        previous = used[index + 1] if index + 1 < len(used) else None
        previous_tokens = (
            previous.total_provider_tokens + previous.total_derived_tokens
            if previous is not None and _has_metered_model_usage(previous)
            else None
        )
        detail = (
            f"    {format_token_count(tokens)} total · "
            f"{format_token_count(summary.total_input_tokens)} input · "
            f"{format_token_count(summary.total_output_tokens)} output · "
            f"change {_delta(tokens, previous_tokens)}"
            if _has_metered_model_usage(summary)
            else _unmetered_history_detail(summary)
        )
        lines.extend(
            (
                f"  {_observed_at(summary)}  {_short_label(model or 'model unavailable')}",
                detail,
                f"    session {_short_label(summary.session_id)}",
            )
        )
    lines.append(f"All sessions: openminion status tokens --recent {requested}")
    return "\n".join(lines)


def _top_model(summary: TokenUsageSummary) -> tuple[str, int]:
    totals: dict[str, int] = defaultdict(int)
    for record in summary.records:
        if record.surface == SURFACE_LLM_TOTAL:
            label = "/".join(part for part in (record.provider, record.model) if part)
            totals[label or "unknown"] += record.total_tokens
    return max(totals.items(), key=lambda item: item[1]) if totals else ("", 0)


def _short_label(value: str, *, limit: int = 34) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[:16]}…{text[-12:]}"


def _observed_at(summary: TokenUsageSummary) -> str:
    value = str(getattr(summary.last_source_event, "observed_at", "") or "")
    if not value:
        return "time unavailable"
    return value.replace("T", " ")[:16]


def _delta(current: int, previous: int | None) -> str:
    if previous is None:
        return "—"
    change = current - previous
    return f"{change:+,}"


def _cost_label(summary: TokenUsageSummary) -> str:
    labels = []
    if summary.has_provider_cost:
        labels.append(
            f"{format_optional_cost_usd(summary.total_provider_cost_usd)} provider"
        )
    if summary.has_estimated_cost:
        labels.append(
            f"{format_optional_cost_usd(summary.total_estimated_cost_usd)} estimated"
        )
    return " · ".join(labels) or "unavailable"


def _history_cost_label(summaries: tuple[TokenUsageSummary, ...]) -> str:
    has_provider = any(summary.has_provider_cost for summary in summaries)
    has_estimated = any(summary.has_estimated_cost for summary in summaries)
    provider = sum(
        summary.total_provider_cost_usd
        for summary in summaries
        if summary.has_provider_cost
    )
    estimated = sum(
        summary.total_estimated_cost_usd
        for summary in summaries
        if summary.has_estimated_cost
    )
    labels = []
    if has_provider:
        labels.append(f"{format_optional_cost_usd(provider)} provider")
    if has_estimated:
        labels.append(f"{format_optional_cost_usd(estimated)} estimated")
    return " · ".join(labels) or "unavailable"


def _call_counts(summary: TokenUsageSummary) -> tuple[int, int, int]:
    coverage = summary.coverage
    return (
        coverage.observed_llm_call_events,
        coverage.unmetered_llm_call_events,
        coverage.failed_llm_call_events,
    )


def _has_metered_model_usage(summary: TokenUsageSummary) -> bool:
    return any(record.surface == SURFACE_LLM_TOTAL for record in summary.records)


def _history_call_coverage(summaries: tuple[TokenUsageSummary, ...]) -> str:
    observed = sum(summary.coverage.observed_llm_call_events for summary in summaries)
    unmetered = sum(summary.coverage.unmetered_llm_call_events for summary in summaries)
    failed = sum(summary.coverage.failed_llm_call_events for summary in summaries)
    return f"Calls: {observed} observed · {unmetered} unmetered · {failed} failed"


def _unmetered_history_detail(summary: TokenUsageSummary) -> str:
    observed, unmetered, failed = _call_counts(summary)
    return (
        f"    tokens unavailable · {observed} observed · "
        f"{unmetered} unmetered · {failed} failed"
    )


__all__ = [
    "TOKENS_USAGE",
    "format_interactive_token_history",
    "format_interactive_token_summary",
    "render_tokens_slash",
]
