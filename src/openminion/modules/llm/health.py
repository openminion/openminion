"""Process-local provider cooldown registry.

A provider that just answered ``RATE_LIMITED`` (or timed out, or failed at the transport) already
told us how long to wait: in ``Retry-After`` when the adapter captured it, otherwise in the backoff
the retry loop would have used. Recording that here keeps later turns from spending an attempt on a
provider that is known to be cooling, and lets routing move to a configured fallback instead.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Iterable, Mapping


@dataclass(frozen=True)
class Cooldown:
    """A recorded provider cooldown."""

    provider: str
    model: str
    reason: str
    retry_after_s: float
    remaining_s: float
    upstream: dict[str, object] = field(default_factory=dict)

    @property
    def until_iso(self) -> str:
        until = datetime.now(timezone.utc) + timedelta(seconds=self.remaining_s)
        return until.isoformat()

    def as_payload(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "model": self.model,
            "reason": self.reason,
            "retry_after_s": self.retry_after_s,
            "remaining_s": round(self.remaining_s, 3),
            "until": self.until_iso,
        }

    def response_details(self) -> dict[str, object]:
        """Upstream provider facts plus the cooldown facts, cooldown keys winning."""
        details = dict(self.upstream)
        details.update(
            {
                "retry_after_s": round(self.retry_after_s, 3),
                "cooldown_remaining_s": round(self.remaining_s, 3),
                "cooldown_reason": self.reason,
            }
        )
        return details


class ProviderHealthRegistry:
    """Thread-safe cooldown bookkeeping for ``(provider, model)`` pairs."""

    def __init__(
        self,
        *,
        max_cooldown_s: float = 900.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._lock = threading.Lock()
        self._max_cooldown_s = max(0.0, float(max_cooldown_s))
        self._clock = clock
        self._streaks: dict[tuple[str, str], int] = {}
        self._until: dict[tuple[str, str], float] = {}
        self._facts: dict[tuple[str, str], tuple[str, float, dict[str, object]]] = {}

    def cooldown(self, provider: str, model: str) -> Cooldown | None:
        """Return the active cooldown for the pair, or ``None`` when it is (now) healthy."""
        key = (str(provider or "").strip(), str(model or "").strip())
        with self._lock:
            until = self._until.get(key)
            if until is None:
                return None
            remaining = until - self._clock()
            if remaining <= 0:
                self._until.pop(key, None)
                self._facts.pop(key, None)
                return None
            reason, retry_after_s, upstream = self._facts.get(key, ("", 0.0, {}))
            return Cooldown(
                provider=key[0],
                model=key[1],
                reason=reason,
                retry_after_s=retry_after_s,
                remaining_s=remaining,
                upstream=dict(upstream),
            )

    def record_failure(
        self,
        provider: str,
        model: str,
        *,
        reason: str,
        retry_after_s: float | None = None,
        backoff_ms: int = 0,
        details: Mapping[str, object] | None = None,
    ) -> Cooldown | None:
        """Record a retryable failure and return the resulting cooldown (``None`` if zero-length)."""
        key = (str(provider or "").strip(), str(model or "").strip())
        honoured = max(0.0, float(retry_after_s or 0.0))
        with self._lock:
            streak = self._streaks.get(key, 0) + 1
            self._streaks[key] = streak
            escalated = (max(0, int(backoff_ms)) / 1000.0) * (2 ** (streak - 1))
            wait = max(honoured, escalated)
            if self._max_cooldown_s:
                wait = min(wait, self._max_cooldown_s)
            if wait <= 0:
                return None
            now = self._clock()
            self._until[key] = now + wait
            upstream = _bounded_details(details)
            self._facts[key] = (str(reason or ""), honoured, upstream)
            return Cooldown(
                provider=key[0],
                model=key[1],
                reason=str(reason or ""),
                retry_after_s=honoured,
                remaining_s=wait,
                upstream=upstream,
            )

    def record_success(self, provider: str, model: str) -> None:
        """Clear cooldown state for the pair after a successful call."""
        key = (str(provider or "").strip(), str(model or "").strip())
        with self._lock:
            self._streaks.pop(key, None)
            self._until.pop(key, None)
            self._facts.pop(key, None)

    def snapshot(self) -> list[dict[str, object]]:
        """Return the currently cooling pairs, newest remaining first."""
        with self._lock:
            keys: Iterable[tuple[str, str]] = list(self._until)
        active: list[Cooldown] = []
        for provider, model in keys:
            cooldown = self.cooldown(provider, model)
            if cooldown is not None:
                active.append(cooldown)
        active.sort(key=lambda item: item.remaining_s, reverse=True)
        return [item.as_payload() for item in active]


def _bounded_details(details: Mapping[str, object] | None) -> dict[str, object]:
    """Keep a small, printable slice of the upstream error facts for the next turn's error."""
    if not isinstance(details, Mapping):
        return {}
    bounded: dict[str, object] = {}
    # Keep the slice small enough to print in an error and to fit the next turn's payload.
    for key, value in details.items():
        if len(bounded) >= 8:
            break
        name = str(key or "").strip()
        if not name or name.startswith("cooldown"):
            continue
        if isinstance(value, (bool, int, float)) or value is None:
            bounded[name] = value
        elif isinstance(value, str):
            bounded[name] = value[:160]
    return bounded


def parse_retry_after_seconds(value: object) -> float | None:
    """Parse a ``Retry-After`` value: delta-seconds or an HTTP-date. ``None`` when unusable."""
    token = str(value or "").strip()
    if not token:
        return None
    try:
        return max(0.0, float(token))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(token)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())


__all__ = [
    "Cooldown",
    "ProviderHealthRegistry",
    "parse_retry_after_seconds",
]


_HEALTH_ATTR = "_provider_health"


def provider_health_for(client: Any) -> ProviderHealthRegistry:
    """Return the cooldown registry owned by the shared client, creating it once."""
    owner = getattr(client, "llmctl", None) or client
    registry = getattr(owner, _HEALTH_ATTR, None)
    if isinstance(registry, ProviderHealthRegistry):
        return registry
    registry = ProviderHealthRegistry()
    setattr(owner, _HEALTH_ATTR, registry)
    return registry


def retry_after_seconds(response: Any) -> float:
    """Read the ``Retry-After`` fact a provider adapter recorded on the error, if any."""
    error = getattr(response, "error", None)
    details = getattr(error, "details", None)
    if not isinstance(details, dict):
        return 0.0
    return parse_retry_after_seconds(details.get("retry_after")) or 0.0


def retry_sleep_seconds(attempt: int, backoff_ms: int, retry_after_s: float) -> float:
    """Sleep long enough for both the configured backoff and the provider's own pacing."""
    escalated: float = max(
        0.0, (max(0, int(backoff_ms)) / 1000.0) * (2 ** max(0, int(attempt)))
    )
    honoured: float = max(0.0, float(retry_after_s or 0.0))
    wait: float = escalated if escalated > honoured else honoured
    return wait


def cooldown_response(
    client: Any, *, provider_name: str, model_name: str, cooldown: Cooldown
) -> Any:
    """Return the typed error a cooling provider produces without spending an attempt."""
    client._emit_operation(
        request=None,
        operation="cooldown",
        provider_name=provider_name,
        model_name=model_name,
        status="skip",
        extra=cooldown.as_payload(),
    )
    return client._error_response(
        provider=provider_name,
        model=model_name,
        code="RATE_LIMITED",
        message=(
            f"Provider {provider_name}/{model_name} is cooling down for "
            f"{cooldown.remaining_s:.1f}s after {cooldown.reason or 'a retryable failure'}"
        ),
        details=cooldown.response_details(),
    )


def begin_attempt(
    registry: ProviderHealthRegistry,
    client: Any,
    provider_name: str,
    model_name: str,
) -> Any | None:
    """Return a typed error when the provider is cooling, otherwise ``None`` to proceed."""
    cooling = registry.cooldown(provider_name, model_name)
    if cooling is None:
        return None
    return cooldown_response(
        client,
        provider_name=provider_name,
        model_name=model_name,
        cooldown=cooling,
    )


def note_failure(
    registry: ProviderHealthRegistry,
    provider_name: str,
    model_name: str,
    response: Any,
    reason: str,
    retryable: bool,
    backoff_ms: int,
) -> float:
    """Record a retryable failure and return the wait the provider asked for (if any)."""
    retry_after_s = retry_after_seconds(response)
    if retryable:
        error = getattr(response, "error", None)
        registry.record_failure(
            provider_name,
            model_name,
            reason=reason,
            retry_after_s=retry_after_s,
            backoff_ms=backoff_ms,
            details=getattr(error, "details", None),
        )
    return retry_after_s
