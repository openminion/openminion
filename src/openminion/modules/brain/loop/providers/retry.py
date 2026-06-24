"""Typed provider retry policy for brain entry calls.

Retry classification stays on infrastructure error codes and provider
categories, never model-output prose.
"""

import random
from dataclasses import dataclass, field
from typing import Any, Callable

from openminion.modules.llm.providers.diagnostics import (
    classify_provider_error_category,
)
from openminion.modules.brain.loop.constants import (
    PROVIDER_RETRYABLE_CATEGORIES,
    PROVIDER_RETRY_DEFAULT_BASE_BACKOFF_MS,
    PROVIDER_RETRY_DEFAULT_JITTER_RATIO,
    PROVIDER_RETRY_DEFAULT_MAX_ATTEMPTS,
    PROVIDER_RETRY_DEFAULT_MAX_BACKOFF_MS,
)


@dataclass(frozen=True)
class ProviderRetryPolicy:
    """Typed retry policy for the entry-call provider exception path.

    `max_attempts` is the TOTAL number of tries (1 initial + N retries),
    so `max_attempts=3` means up to 2 retries after the first failure.
    """

    max_attempts: int = PROVIDER_RETRY_DEFAULT_MAX_ATTEMPTS
    base_backoff_ms: float = PROVIDER_RETRY_DEFAULT_BASE_BACKOFF_MS
    max_backoff_ms: float = PROVIDER_RETRY_DEFAULT_MAX_BACKOFF_MS
    jitter_ratio: float = PROVIDER_RETRY_DEFAULT_JITTER_RATIO
    retryable_categories: frozenset[str] = field(default=PROVIDER_RETRYABLE_CATEGORIES)

    @property
    def max_retries(self) -> int:
        """Retries after the first attempt (loop-bound convenience)."""
        return max(0, int(self.max_attempts) - 1)


def classify_retryable(exc: Exception) -> tuple[str, bool]:
    """Return `(category, is_retryable)` for a provider exception.

    Mirrors `loop/failures.py`: prefer the typed `exc.code` when present,
    else delegate to the canonical category classifier. `is_retryable`
    is decided against :data:`PROVIDER_RETRYABLE_CATEGORIES`.
    """
    code = str(getattr(exc, "code", "") or "").strip().upper()
    if not code:
        details = dict(getattr(exc, "details", {}) or {})
        code = classify_provider_error_category(
            error=exc,
            response_text=str(
                details.get("response_text") or details.get("body_text") or ""
            ),
        )
    code = str(code or "").strip().upper()
    return code, code in PROVIDER_RETRYABLE_CATEGORIES


def is_retryable(exc: Exception) -> bool:
    """Convenience predicate: True when the exception category is transient."""
    return classify_retryable(exc)[1]


def compute_backoff_ms(
    policy: ProviderRetryPolicy,
    attempt: int,
    *,
    rand: Callable[[], float] = random.random,
) -> float:
    """Exponential backoff with bounded jitter for retry `attempt`.

    `attempt` is the 0-indexed retry number (0 = first retry). Returns a
    millisecond delay capped at `policy.max_backoff_ms`, with jitter in
    `[-jitter_ratio, +jitter_ratio]` applied to the capped base. The
    `rand` seam lets tests pin the jitter deterministically.
    """
    safe_attempt = max(0, int(attempt))
    raw = policy.base_backoff_ms * (2.0**safe_attempt)
    capped = min(float(policy.max_backoff_ms), raw)
    # jitter in [-jitter_ratio, +jitter_ratio]; rand() in [0,1) → [-1,1).
    jitter_span = policy.jitter_ratio * capped
    jitter = (rand() * 2.0 - 1.0) * jitter_span
    return max(0.0, capped + jitter)


def build_provider_retry_policy(config: Any = None) -> ProviderRetryPolicy:
    """Construct a policy, honoring `runtime.provider_retry_max_attempts`
    when present. Defensive: any missing/invalid config falls back to the
    bounded defaults so a misconfigured runtime never disables retries
    entirely (min 1 attempt) nor unbounds them."""
    max_attempts = PROVIDER_RETRY_DEFAULT_MAX_ATTEMPTS
    runtime = getattr(config, "runtime", None)
    raw = getattr(runtime, "provider_retry_max_attempts", None)
    if raw is not None:
        try:
            parsed = int(raw)
        except (TypeError, ValueError):
            parsed = PROVIDER_RETRY_DEFAULT_MAX_ATTEMPTS
        # Clamp to a sane band: at least 1 try, at most 6 (avoid runaway
        # retry storms against a hard-down provider).
        max_attempts = max(1, min(6, parsed))
    return ProviderRetryPolicy(max_attempts=max_attempts)


__all__ = [
    "PROVIDER_RETRYABLE_CATEGORIES",
    "ProviderRetryPolicy",
    "build_provider_retry_policy",
    "classify_retryable",
    "compute_backoff_ms",
    "is_retryable",
]
