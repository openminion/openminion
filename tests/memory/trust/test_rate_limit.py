from __future__ import annotations

from datetime import datetime, timedelta, timezone

from openminion.modules.memory.trust import PromotionRateLimiter, RateLimit


def test_rate_limiter_returns_typed_rate_limited_with_retry_after_hint() -> None:
    limiter = PromotionRateLimiter(
        {
            "llm_extracted": RateLimit(
                source_class="llm_extracted",
                window_seconds=10,
                max_promotions=2,
            )
        }
    )
    start = datetime(2026, 5, 21, 12, 0, 0, tzinfo=timezone.utc)
    limiter.record("llm_extracted", at=start)
    limiter.record("llm_extracted", at=start + timedelta(seconds=1))

    decision = limiter.assess("llm_extracted", at=start + timedelta(seconds=2))

    assert decision.allowed is False
    assert decision.reason_code == "RATE_LIMITED"
    assert decision.retry_after_seconds == 8
    assert decision.retry_after_ms == 8000
    assert decision.observed_promotions == 2
    assert decision.max_promotions == 2


def test_rate_limiter_resets_after_window_boundary() -> None:
    limiter = PromotionRateLimiter(
        {
            "agent_inferred": RateLimit(
                source_class="agent_inferred",
                window_seconds=10,
                max_promotions=1,
            )
        }
    )
    start = datetime(2026, 5, 21, 12, 0, 0, tzinfo=timezone.utc)
    limiter.record("agent_inferred", at=start)

    blocked = limiter.assess("agent_inferred", at=start + timedelta(seconds=5))
    reset = limiter.assess("agent_inferred", at=start + timedelta(seconds=11))

    assert blocked.allowed is False
    assert blocked.reason_code == "RATE_LIMITED"
    assert reset.allowed is True
    assert reset.reason_code == "ALLOWED"


def test_rate_limiter_check_and_record_allows_unlimited_user_input() -> None:
    limiter = PromotionRateLimiter(
        {
            "user_input": RateLimit(
                source_class="user_input",
                window_seconds=3600,
                max_promotions=None,
            )
        }
    )
    start = datetime(2026, 5, 21, 12, 0, 0, tzinfo=timezone.utc)

    first = limiter.check_and_record("user_input", at=start)
    second = limiter.check_and_record("user_input", at=start + timedelta(seconds=1))

    assert first.allowed is True
    assert first.reason_code == "ALLOWED"
    assert second.allowed is True
    assert second.reason_code == "ALLOWED"
    assert second.max_promotions is None
