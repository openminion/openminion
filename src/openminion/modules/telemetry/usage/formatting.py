"""Shared presentation helpers for telemetry usage summaries."""

from __future__ import annotations

from openminion.modules.telemetry.usage.types import RunStats, SessionStatsSummary


def _format_duration_ms(duration_ms: int) -> str:
    if duration_ms <= 0:
        return "0.0s"
    seconds = duration_ms / 1000.0
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    remainder = seconds - (minutes * 60)
    return f"{minutes}m {remainder:.1f}s"


def format_run_stats_footer(stats: RunStats | None) -> str:
    if stats is None or not stats.has_any_data:
        return ""
    cache_segment = (
        f" cache {stats.cache_read_tokens}" if stats.cache_read_tokens > 0 else ""
    )
    return (
        "[tokens "
        f"{stats.input_tokens}/{stats.output_tokens}"
        f"{cache_segment} | calls {stats.llm_calls} llm, "
        f"{stats.tool_calls} tools ({stats.tool_errors} err) | "
        f"{_format_duration_ms(stats.duration_ms)}]"
    )


def format_session_stats_summary(summary: SessionStatsSummary) -> str:
    lines = [
        f"session {summary.session_id}",
        f"turns {summary.turn_count}",
        "totals " + format_run_stats_footer(summary.stats).strip("[]"),
    ]
    top_tools = ", ".join(f"{item.name} {item.calls}" for item in summary.top_tools)
    lines.append(f"top tools {top_tools}" if top_tools else "top tools -")
    return "\n".join(lines)
