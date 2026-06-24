from __future__ import annotations

from .metrics import APIRequestMetrics


_API_METRICS = APIRequestMetrics()


def observe(
    *, route: str, status_code: int, duration_ms: int, error_code: str | None = None
) -> None:
    _API_METRICS.observe(
        route=route,
        status_code=int(status_code),
        duration_ms=int(duration_ms),
        error_code=error_code,
    )


def snapshot(*, reset: bool = False) -> dict:
    data = _API_METRICS.snapshot()
    if reset:
        _API_METRICS.reset()
    return data


def consistency_stamp() -> dict:
    return _API_METRICS.consistency_stamp()


def reset() -> None:
    _API_METRICS.reset()
