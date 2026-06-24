"""Per-source-class rate-limit primitives for memory promotion."""

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import ceil
from typing import Literal

from openminion.modules.memory.errors import InvalidArgumentError

from .constants import (
    DEFAULT_AGENT_INFERRED_MAX_PROMOTIONS,
    DEFAULT_IMPORTED_BUNDLE_MAX_PROMOTIONS,
    DEFAULT_LLM_EXTRACTED_MAX_PROMOTIONS,
    DEFAULT_RATE_LIMIT_WINDOW_SECONDS,
    DEFAULT_TOOL_RESULT_MAX_PROMOTIONS,
    DEFAULT_USER_INPUT_MAX_PROMOTIONS,
    IMPORTED_BUNDLE_RATE_LIMIT_WINDOW_SECONDS,
)
from .types import MemorySourceClass

RateLimitReasonCode = Literal["ALLOWED", "RATE_LIMITED"]
_DEFAULT_MAX_PROMOTIONS: dict[MemorySourceClass, int | None] = {
    "user_input": DEFAULT_USER_INPUT_MAX_PROMOTIONS,
    "tool_result": DEFAULT_TOOL_RESULT_MAX_PROMOTIONS,
    "llm_extracted": DEFAULT_LLM_EXTRACTED_MAX_PROMOTIONS,
    "agent_inferred": DEFAULT_AGENT_INFERRED_MAX_PROMOTIONS,
    "imported_bundle": DEFAULT_IMPORTED_BUNDLE_MAX_PROMOTIONS,
}


def _coerce_dt(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


@dataclass(frozen=True)
class RateLimit:
    source_class: MemorySourceClass
    window_seconds: int
    max_promotions: int | None

    def __post_init__(self) -> None:
        if int(self.window_seconds) < 0:
            raise InvalidArgumentError("window_seconds must be >= 0")
        if self.max_promotions is not None and int(self.max_promotions) <= 0:
            raise InvalidArgumentError("max_promotions must be positive when set")

    @property
    def is_unlimited(self) -> bool:
        return self.max_promotions is None


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    reason_code: RateLimitReasonCode
    retry_after_seconds: int | None = None
    retry_after_ms: int | None = None
    observed_promotions: int = 0
    max_promotions: int | None = None


def default_rate_limits() -> dict[MemorySourceClass, RateLimit]:
    return {
        source_class: RateLimit(
            source_class=source_class,
            window_seconds=(
                IMPORTED_BUNDLE_RATE_LIMIT_WINDOW_SECONDS
                if source_class == "imported_bundle"
                else DEFAULT_RATE_LIMIT_WINDOW_SECONDS
            ),
            max_promotions=max_promotions,
        )
        for source_class, max_promotions in _DEFAULT_MAX_PROMOTIONS.items()
    }


class PromotionRateLimiter:
    """Track promotion counts per source class within configured windows."""

    def __init__(
        self, policies: dict[MemorySourceClass, RateLimit] | None = None
    ) -> None:
        self._policies = dict(policies or default_rate_limits())
        self._events: dict[MemorySourceClass, deque[datetime]] = defaultdict(deque)

    def _windowed_events(
        self,
        policy: RateLimit,
        *,
        at: datetime,
    ) -> deque[datetime]:
        events = self._events[policy.source_class]
        if policy.is_unlimited:
            return events
        window_start = at - timedelta(seconds=policy.window_seconds)
        while events and events[0] < window_start:
            events.popleft()
        return events

    def _retry_after_seconds(
        self,
        policy: RateLimit,
        *,
        at: datetime,
        events: deque[datetime],
    ) -> int | None:
        if policy.is_unlimited or not events:
            return None
        if policy.window_seconds == 0:
            return 0
        earliest = events[0] + timedelta(seconds=policy.window_seconds)
        remaining = max(0.0, (earliest - at).total_seconds())
        return int(ceil(remaining))

    def assess(
        self,
        source_class: MemorySourceClass,
        *,
        at: datetime | str,
    ) -> RateLimitDecision:
        policy = self._policies[source_class]
        effective_at = _coerce_dt(at)
        events = self._windowed_events(policy, at=effective_at)
        observed = len(events)
        if policy.is_unlimited:
            return RateLimitDecision(
                allowed=True,
                reason_code="ALLOWED",
                observed_promotions=observed,
                max_promotions=None,
            )
        assert policy.max_promotions is not None
        if observed >= policy.max_promotions:
            retry_after_seconds = self._retry_after_seconds(
                policy,
                at=effective_at,
                events=events,
            )
            return RateLimitDecision(
                allowed=False,
                reason_code="RATE_LIMITED",
                retry_after_seconds=retry_after_seconds,
                retry_after_ms=(
                    None
                    if retry_after_seconds is None
                    else int(retry_after_seconds * 1000)
                ),
                observed_promotions=observed,
                max_promotions=policy.max_promotions,
            )
        return RateLimitDecision(
            allowed=True,
            reason_code="ALLOWED",
            observed_promotions=observed,
            max_promotions=policy.max_promotions,
        )

    def record(
        self,
        source_class: MemorySourceClass,
        *,
        at: datetime | str,
    ) -> None:
        policy = self._policies[source_class]
        if policy.is_unlimited:
            return
        effective_at = _coerce_dt(at)
        events = self._windowed_events(policy, at=effective_at)
        events.append(effective_at)

    def check_and_record(
        self,
        source_class: MemorySourceClass,
        *,
        at: datetime | str,
    ) -> RateLimitDecision:
        decision = self.assess(source_class, at=at)
        if decision.allowed:
            self.record(source_class, at=at)
        return decision
