"""Unit tests for the typed provider retry policy."""

from __future__ import annotations

import pytest

from openminion.modules.llm.errors import LLMCtlError
from openminion.modules.brain.loop.providers.retry import (
    PROVIDER_RETRYABLE_CATEGORIES,
    ProviderRetryPolicy,
    build_provider_retry_policy,
    classify_retryable,
    compute_backoff_ms,
    is_retryable,
)


def test_retryable_categories_are_the_transient_three() -> None:
    assert PROVIDER_RETRYABLE_CATEGORIES == frozenset(
        {"RATE_LIMITED", "TIMEOUT", "PROVIDER_ERROR"}
    )


@pytest.mark.parametrize(
    ("error_code", "message", "expected_retry"),
    [
        ("PROVIDER_ERROR", "Bad Gateway", True),
        ("RATE_LIMITED", "429", True),
        ("TIMEOUT", "timed out", True),
        ("INVALID_ARGUMENT", "400", False),
        ("AUTH_ERROR", "401", False),
    ],
)
def test_classify_retryable_llm_errors(
    error_code: str, message: str, expected_retry: bool
) -> None:
    cat, retry = classify_retryable(LLMCtlError(error_code, message))
    assert cat == error_code
    assert retry is expected_retry


def test_is_retryable_convenience_predicate() -> None:
    assert is_retryable(LLMCtlError("TIMEOUT", "x")) is True
    assert is_retryable(LLMCtlError("AUTH_ERROR", "x")) is False


def test_unknown_code_falls_through_classifier() -> None:
    cat, retry = classify_retryable(RuntimeError("mystery"))
    assert isinstance(cat, str)
    assert isinstance(retry, bool)


def test_backoff_is_exponential_with_zero_jitter() -> None:
    policy = ProviderRetryPolicy()
    assert compute_backoff_ms(policy, 0, rand=lambda: 0.5) == 250.0
    assert compute_backoff_ms(policy, 1, rand=lambda: 0.5) == 500.0
    assert compute_backoff_ms(policy, 2, rand=lambda: 0.5) == 1000.0


def test_backoff_is_capped_at_max() -> None:
    policy = ProviderRetryPolicy(max_backoff_ms=600.0)
    assert compute_backoff_ms(policy, 2, rand=lambda: 0.5) == 600.0
    assert compute_backoff_ms(policy, 50, rand=lambda: 0.5) == 600.0


def test_backoff_jitter_bounds() -> None:
    policy = ProviderRetryPolicy(jitter_ratio=0.25)
    low = compute_backoff_ms(policy, 0, rand=lambda: 0.0)
    high = compute_backoff_ms(policy, 0, rand=lambda: 1.0)
    assert low == 187.5
    assert high == 312.5
    assert low < 250.0 < high


def test_backoff_never_negative() -> None:
    policy = ProviderRetryPolicy(jitter_ratio=2.0)
    assert compute_backoff_ms(policy, 0, rand=lambda: 0.0) == 0.0


def test_negative_attempt_treated_as_zero() -> None:
    policy = ProviderRetryPolicy()
    assert compute_backoff_ms(policy, -5, rand=lambda: 0.5) == 250.0


def test_max_retries_is_attempts_minus_one() -> None:
    assert ProviderRetryPolicy(max_attempts=3).max_retries == 2
    assert ProviderRetryPolicy(max_attempts=1).max_retries == 0
    assert ProviderRetryPolicy(max_attempts=0).max_retries == 0


def test_default_factory_returns_three_attempts() -> None:
    assert build_provider_retry_policy(None).max_attempts == 3


def test_factory_honors_config_knob() -> None:
    class _Runtime:
        provider_retry_max_attempts = 5

    class _Config:
        runtime = _Runtime()

    assert build_provider_retry_policy(_Config()).max_attempts == 5


def test_factory_clamps_to_sane_band() -> None:
    class _Runtime:
        provider_retry_max_attempts = 999

    class _Config:
        runtime = _Runtime()

    assert build_provider_retry_policy(_Config()).max_attempts == 6

    class _RuntimeZero:
        provider_retry_max_attempts = 0

    class _ConfigZero:
        runtime = _RuntimeZero()

    assert build_provider_retry_policy(_ConfigZero()).max_attempts == 1


def test_factory_tolerates_garbage_config_value() -> None:
    class _Runtime:
        provider_retry_max_attempts = "not-an-int"

    class _Config:
        runtime = _Runtime()

    assert build_provider_retry_policy(_Config()).max_attempts == 3
