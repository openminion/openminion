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
    max_attempts: int = PROVIDER_RETRY_DEFAULT_MAX_ATTEMPTS
    base_backoff_ms: float = PROVIDER_RETRY_DEFAULT_BASE_BACKOFF_MS
    max_backoff_ms: float = PROVIDER_RETRY_DEFAULT_MAX_BACKOFF_MS
    jitter_ratio: float = PROVIDER_RETRY_DEFAULT_JITTER_RATIO
    retryable_categories: frozenset[str] = field(default=PROVIDER_RETRYABLE_CATEGORIES)

    @property
    def max_retries(self) -> int:
        return max(0, int(self.max_attempts) - 1)


def classify_retryable(exc: Exception) -> tuple[str, bool]:
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
    return classify_retryable(exc)[1]


def compute_backoff_ms(
    policy: ProviderRetryPolicy,
    attempt: int,
    *,
    rand: Callable[[], float] = random.random,
) -> float:
    safe_attempt = max(0, int(attempt))
    raw = policy.base_backoff_ms * (2.0**safe_attempt)
    capped = min(float(policy.max_backoff_ms), raw)
    jitter_span = policy.jitter_ratio * capped
    jitter = (rand() * 2.0 - 1.0) * jitter_span
    return max(0.0, capped + jitter)


def build_provider_retry_policy(config: Any = None) -> ProviderRetryPolicy:
    runtime = getattr(config, "runtime", None)
    raw = getattr(
        runtime, "provider_retry_max_attempts", PROVIDER_RETRY_DEFAULT_MAX_ATTEMPTS
    )
    try:
        max_attempts = int(raw)
    except (TypeError, ValueError):
        max_attempts = PROVIDER_RETRY_DEFAULT_MAX_ATTEMPTS
    return ProviderRetryPolicy(max_attempts=max(1, min(6, max_attempts)))


__all__ = [
    "PROVIDER_RETRYABLE_CATEGORIES",
    "ProviderRetryPolicy",
    "build_provider_retry_policy",
    "classify_retryable",
    "compute_backoff_ms",
    "is_retryable",
]
