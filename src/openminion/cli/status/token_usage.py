from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Mapping


_PROMPT_KEYS = (
    "usage_prompt_tokens",
    "prompt_tokens",
    "input_tokens",
    "total_input_tokens_used",
)
_COMPLETION_KEYS = (
    "usage_completion_tokens",
    "completion_tokens",
    "output_tokens",
    "total_output_tokens_used",
)
_TOTAL_KEYS = (
    "usage_total_tokens",
    "total_tokens",
    "total_tokens_used",
)
_CACHED_KEYS = (
    "usage_cached_tokens",
    "cached_tokens",
    "cache_read_input_tokens",
)


@dataclass(frozen=True)
class TokenUsageTotals:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cached_tokens: int | None = None

    @property
    def is_empty(self) -> bool:
        return (
            self.prompt_tokens is None
            and self.completion_tokens is None
            and self.total_tokens is None
            and self.cached_tokens is None
        )


@dataclass(frozen=True)
class TokenUsageSnapshot:
    turn_prompt_tokens: int | None = None
    turn_completion_tokens: int | None = None
    turn_total_tokens: int | None = None
    session_prompt_tokens: int | None = None
    session_completion_tokens: int | None = None
    session_total_tokens: int | None = None
    context_used_tokens: int | None = None
    context_limit_tokens: int | None = None
    has_live_deltas: bool = False
    turn_elapsed_seconds: float | None = None
    updated_at_monotonic: float | None = None
    turn_cached_tokens: int | None = None
    session_cached_tokens: int | None = None

    @property
    def context_pct(self) -> int | None:
        used = self.context_used_tokens
        limit = self.context_limit_tokens
        if used is None or limit is None or limit <= 0:
            return None
        return max(0, int(round((used / limit) * 100)))

    @property
    def has_any_usage(self) -> bool:
        return any(
            value is not None
            for value in (
                self.turn_total_tokens,
                self.session_total_tokens,
                self.context_used_tokens,
            )
        )


def usage_totals_from_mapping(
    payload: Mapping[str, Any] | None,
) -> TokenUsageTotals | None:
    if not isinstance(payload, Mapping):
        return None
    prompt_tokens = _first_int(payload, _PROMPT_KEYS)
    completion_tokens = _first_int(payload, _COMPLETION_KEYS)
    total_tokens = _first_int(payload, _TOTAL_KEYS)
    cached_tokens = _first_int(payload, _CACHED_KEYS)
    if total_tokens is None and (
        prompt_tokens is not None or completion_tokens is not None
    ):
        total_tokens = max(0, int(prompt_tokens or 0) + int(completion_tokens or 0))
    totals = TokenUsageTotals(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        cached_tokens=cached_tokens,
    )
    return None if totals.is_empty else totals


def accumulate_usage(
    previous: TokenUsageTotals | None,
    increment: TokenUsageTotals | None,
) -> TokenUsageTotals | None:
    if increment is None or increment.is_empty:
        return previous
    if previous is None or previous.is_empty:
        return increment
    return TokenUsageTotals(
        prompt_tokens=_sum_optional(previous.prompt_tokens, increment.prompt_tokens),
        completion_tokens=_sum_optional(
            previous.completion_tokens,
            increment.completion_tokens,
        ),
        total_tokens=_sum_optional(previous.total_tokens, increment.total_tokens),
        cached_tokens=_sum_optional(previous.cached_tokens, increment.cached_tokens),
    )


def build_token_usage_snapshot(
    *,
    turn: TokenUsageTotals | None,
    session: TokenUsageTotals | None,
    context_used_tokens: int | None,
    context_limit_tokens: int | None,
    has_live_deltas: bool,
    turn_elapsed_seconds: float | None,
    updated_at_monotonic: float | None,
) -> TokenUsageSnapshot:
    return TokenUsageSnapshot(
        turn_prompt_tokens=getattr(turn, "prompt_tokens", None),
        turn_completion_tokens=getattr(turn, "completion_tokens", None),
        turn_total_tokens=getattr(turn, "total_tokens", None),
        session_prompt_tokens=getattr(session, "prompt_tokens", None),
        session_completion_tokens=getattr(session, "completion_tokens", None),
        session_total_tokens=getattr(session, "total_tokens", None),
        context_used_tokens=context_used_tokens,
        context_limit_tokens=context_limit_tokens,
        has_live_deltas=bool(has_live_deltas),
        turn_elapsed_seconds=turn_elapsed_seconds,
        updated_at_monotonic=updated_at_monotonic,
        turn_cached_tokens=getattr(turn, "cached_tokens", None),
        session_cached_tokens=getattr(session, "cached_tokens", None),
    )


def format_token_usage_summary(
    snapshot: TokenUsageSnapshot | None,
    *,
    now_monotonic: float | None = None,
) -> str:
    timing = format_token_usage_timing(snapshot, now_monotonic=now_monotonic)
    if snapshot is None:
        return ""
    if not _has_meaningful_usage_facts(snapshot):
        return timing
    turn = format_token_count(_displayable_usage_value(snapshot.turn_total_tokens))
    session = format_token_count(
        _displayable_usage_value(snapshot.session_total_tokens)
    )
    context = format_context_window(snapshot)
    cached = snapshot.turn_cached_tokens
    cache_suffix = (
        f" ({format_token_count(cached)} cached)" if cached is not None else ""
    )
    summary = f"turn {turn}{cache_suffix}   session {session}   ctx {context}"
    return f"{summary}   {timing}" if timing else summary


def format_token_usage_timing(
    snapshot: TokenUsageSnapshot | None,
    *,
    now_monotonic: float | None = None,
) -> str:
    if snapshot is None:
        return ""
    parts: list[str] = []
    elapsed_text = format_elapsed_duration(snapshot.turn_elapsed_seconds)
    if elapsed_text:
        parts.append(f"total {elapsed_text}")
    age_text = format_snapshot_relative_age(
        snapshot,
        now_monotonic=now_monotonic,
    )
    if age_text:
        parts.append(age_text)
    return "   ".join(parts)


def format_elapsed_duration(value: float | None) -> str:
    if value is None:
        return ""
    total_seconds = max(0, int(float(value)))
    if total_seconds < 60:
        return f"{total_seconds}s"
    minutes, seconds = divmod(total_seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds}s"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h"


def format_relative_age(value: float | None) -> str:
    if value is None:
        return ""
    seconds = max(0, int(float(value)))
    if seconds <= 5:
        return "just now"
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def format_snapshot_relative_age(
    snapshot: TokenUsageSnapshot | None,
    *,
    now_monotonic: float | None = None,
) -> str:
    if snapshot is None or snapshot.updated_at_monotonic is None:
        return ""
    reference_now = time.monotonic() if now_monotonic is None else float(now_monotonic)
    age_seconds = max(0.0, reference_now - float(snapshot.updated_at_monotonic))
    return format_relative_age(age_seconds)


def format_token_count(value: int | None) -> str:
    if value is None:
        return "—"
    number = max(0, int(value))
    if number < 1000:
        return str(number)
    if number < 1_000_000:
        text = f"{number / 1000:.1f}k"
    else:
        text = f"{number / 1_000_000:.1f}m"
    return text.replace(".0", "")


def format_context_window(snapshot: TokenUsageSnapshot | None) -> str:
    if snapshot is None or snapshot.context_used_tokens is None:
        return "—"
    used = snapshot.context_used_tokens
    limit = snapshot.context_limit_tokens
    used_text = format_token_count(used)
    if limit is None or limit <= 0:
        return used_text
    limit_text = format_token_count(limit)
    pct = snapshot.context_pct
    if pct is None:
        return f"{used_text} / {limit_text}"
    return f"{used_text} / {limit_text} ({pct}%)"


def _displayable_usage_value(value: int | None) -> int | None:
    if value is None:
        return None
    normalized = max(0, int(value))
    return None if normalized == 0 else normalized


def _has_meaningful_usage_facts(snapshot: TokenUsageSnapshot) -> bool:
    return any(
        value is not None
        for value in (
            _displayable_usage_value(snapshot.turn_total_tokens),
            _displayable_usage_value(snapshot.session_total_tokens),
            snapshot.context_used_tokens,
            snapshot.context_limit_tokens,
        )
    )


def _first_int(payload: Mapping[str, Any], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        coerced = _coerce_int(payload.get(key))
        if coerced is not None:
            return coerced
    return None


def _coerce_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _sum_optional(left: int | None, right: int | None) -> int | None:
    if left is None and right is None:
        return None
    return int(left or 0) + int(right or 0)


__all__ = [
    "TokenUsageSnapshot",
    "TokenUsageTotals",
    "accumulate_usage",
    "build_token_usage_snapshot",
    "format_elapsed_duration",
    "format_context_window",
    "format_relative_age",
    "format_snapshot_relative_age",
    "format_token_count",
    "format_token_usage_summary",
    "format_token_usage_timing",
    "usage_totals_from_mapping",
]
