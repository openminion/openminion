"""Request and turn metrics for the developer API."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from math import ceil
from threading import Lock
from time import perf_counter

_STATUS_BUCKETS = ("1xx", "2xx", "3xx", "4xx", "5xx")
_TURN_LATENCY_ROLLING_WINDOW_SIZE = 128
_REQUEST_RATE_WINDOW_SECONDS = 60
_TOP_ERROR_CODES_LIMIT = 5
_TURN_TIMEOUT_ERROR_CODE = "turn_timeout"
_METRICS_SCHEMA_NAME = "openminion.api.metrics"
_METRICS_SCHEMA_VERSION = 1
_TURNS_ROUTE = "POST /turns"


@dataclass
class _TurnLatencyAggregate:
    count: int = 0
    total_ms: int = 0
    min_ms: int = 0
    max_ms: int = 0

    def observe(self, duration_ms: int) -> None:
        bounded_duration = max(0, int(duration_ms))
        self.count += 1
        self.total_ms += bounded_duration
        if self.count == 1:
            self.min_ms = bounded_duration
            self.max_ms = bounded_duration
            return
        self.min_ms = min(self.min_ms, bounded_duration)
        self.max_ms = max(self.max_ms, bounded_duration)

    def snapshot(self) -> dict:
        average = int(self.total_ms / self.count) if self.count else 0
        return {
            "count": self.count,
            "avg": average,
            "min": self.min_ms if self.count else 0,
            "max": self.max_ms if self.count else 0,
        }

    def reset(self) -> None:
        self.count = 0
        self.total_ms = 0
        self.min_ms = 0
        self.max_ms = 0


class _RollingLatencyWindow:
    def __init__(self, *, size: int) -> None:
        self._samples: deque[int] = deque(maxlen=max(1, int(size)))

    def observe(self, duration_ms: int) -> None:
        self._samples.append(max(0, int(duration_ms)))

    def snapshot(self) -> dict:
        ordered = sorted(self._samples)
        if not ordered:
            return {
                "size": int(self._samples.maxlen or 0),
                "count": 0,
                "p50": 0,
                "p95": 0,
                "min": 0,
                "max": 0,
            }

        return {
            "size": int(self._samples.maxlen or 0),
            "count": len(ordered),
            "p50": _nearest_rank_percentile(ordered, 0.50),
            "p95": _nearest_rank_percentile(ordered, 0.95),
            "min": ordered[0],
            "max": ordered[-1],
        }

    def reset(self) -> None:
        self._samples.clear()


class APIRequestMetrics:
    def __init__(self) -> None:
        self._lock = Lock()
        self._runtime_started_at_utc = datetime.now(tz=timezone.utc).isoformat()
        self._runtime_started_at_perf = perf_counter()
        self._counters_reset_at_utc = self._runtime_started_at_utc
        self._counters_reset_at_perf = self._runtime_started_at_perf
        self._total_requests = 0
        self._by_route: dict[str, int] = {}
        self._by_route_status_classes: dict[str, dict[str, int]] = {}
        self._status_classes = {bucket: 0 for bucket in _STATUS_BUCKETS}
        self._error_codes: dict[str, int] = {}
        self._error_codes_by_route: dict[str, dict[str, int]] = {}
        self._turn_latency = _TurnLatencyAggregate()
        self._turn_latency_rolling = _RollingLatencyWindow(
            size=_TURN_LATENCY_ROLLING_WINDOW_SIZE
        )
        self._turn_timeouts_total = 0
        self._turn_timeouts_by_route: dict[str, int] = {}
        self._request_timestamps: deque[float] = deque()

    def observe(
        self,
        *,
        route: str,
        status_code: int,
        duration_ms: int,
        error_code: str | None = None,
    ) -> None:
        status_bucket = _status_class_bucket(status_code)
        with self._lock:
            now = perf_counter()
            _prune_request_timestamps(
                timestamps=self._request_timestamps,
                now=now,
                window_seconds=_REQUEST_RATE_WINDOW_SECONDS,
            )
            self._request_timestamps.append(now)
            self._total_requests += 1
            self._by_route[route] = self._by_route.get(route, 0) + 1
            self._status_classes[status_bucket] += 1
            route_status = self._by_route_status_classes.setdefault(
                route,
                {bucket: 0 for bucket in _STATUS_BUCKETS},
            )
            route_status[status_bucket] += 1
            if error_code:
                self._error_codes[error_code] = self._error_codes.get(error_code, 0) + 1
                route_errors = self._error_codes_by_route.setdefault(route, {})
                route_errors[error_code] = route_errors.get(error_code, 0) + 1
                if route == _TURNS_ROUTE and error_code == _TURN_TIMEOUT_ERROR_CODE:
                    self._turn_timeouts_total += 1
                    self._turn_timeouts_by_route[route] = (
                        self._turn_timeouts_by_route.get(route, 0) + 1
                    )
            if route == _TURNS_ROUTE:
                self._turn_latency.observe(duration_ms)
                self._turn_latency_rolling.observe(duration_ms)

    def snapshot(self) -> dict:
        with self._lock:
            snapshot_utc = datetime.now(tz=timezone.utc).isoformat()
            now = perf_counter()
            _prune_request_timestamps(
                timestamps=self._request_timestamps,
                now=now,
                window_seconds=_REQUEST_RATE_WINDOW_SECONDS,
            )
            request_count_1m = len(self._request_timestamps)
            rps_1m = round(request_count_1m / float(_REQUEST_RATE_WINDOW_SECONDS), 4)
            turn_latency = self._turn_latency.snapshot()
            turn_latency["rolling_window"] = self._turn_latency_rolling.snapshot()
            error_total = int(sum(self._error_codes.values()))
            turn_requests_total = int(self._by_route.get(_TURNS_ROUTE, 0))
            timeout_ratio = (
                round(self._turn_timeouts_total / float(turn_requests_total), 4)
                if turn_requests_total > 0
                else 0.0
            )
            uptime_ms = max(
                0, int((perf_counter() - self._runtime_started_at_perf) * 1000)
            )
            since_reset_ms = max(
                0, int((perf_counter() - self._counters_reset_at_perf) * 1000)
            )
            return {
                "snapshot_utc": snapshot_utc,
                "consistency": _build_consistency_stamp(
                    runtime_started_at_utc=self._runtime_started_at_utc,
                    reset_at_utc=self._counters_reset_at_utc,
                ),
                "schema": {
                    "name": _METRICS_SCHEMA_NAME,
                    "version": _METRICS_SCHEMA_VERSION,
                },
                "runtime": {
                    "started_at_utc": self._runtime_started_at_utc,
                    "uptime_ms": uptime_ms,
                },
                "reset": {
                    "reset_at_utc": self._counters_reset_at_utc,
                    "since_reset_ms": since_reset_ms,
                },
                "requests": {
                    "total": self._total_requests,
                    "by_route": dict(sorted(self._by_route.items())),
                    "by_route_status_classes": {
                        route: dict(self._by_route_status_classes[route])
                        for route in sorted(self._by_route_status_classes.keys())
                    },
                    "status_classes": dict(self._status_classes),
                    "rate": {
                        "window_seconds": _REQUEST_RATE_WINDOW_SECONDS,
                        "count": request_count_1m,
                        "rps_1m": rps_1m,
                    },
                },
                "errors": {
                    "total": error_total,
                    "by_code": dict(sorted(self._error_codes.items())),
                    "by_route": {
                        route: dict(sorted(self._error_codes_by_route[route].items()))
                        for route in sorted(self._error_codes_by_route.keys())
                    },
                    "top_error_codes": _top_error_codes(
                        error_codes=self._error_codes,
                        total_errors=error_total,
                        limit=_TOP_ERROR_CODES_LIMIT,
                    ),
                },
                "turn_timeouts": {
                    "total": self._turn_timeouts_total,
                    "by_route": dict(sorted(self._turn_timeouts_by_route.items())),
                    "turn_requests_total": turn_requests_total,
                    "timeout_ratio": timeout_ratio,
                },
                "turn_latency_ms": turn_latency,
            }

    def consistency_stamp(self) -> dict:
        with self._lock:
            return _build_consistency_stamp(
                runtime_started_at_utc=self._runtime_started_at_utc,
                reset_at_utc=self._counters_reset_at_utc,
            )

    def reset(self) -> None:
        with self._lock:
            self._total_requests = 0
            self._by_route.clear()
            self._by_route_status_classes.clear()
            self._status_classes = {bucket: 0 for bucket in _STATUS_BUCKETS}
            self._error_codes.clear()
            self._error_codes_by_route.clear()
            self._turn_timeouts_total = 0
            self._turn_timeouts_by_route.clear()
            self._turn_latency.reset()
            self._turn_latency_rolling.reset()
            self._request_timestamps.clear()
            self._counters_reset_at_utc = datetime.now(tz=timezone.utc).isoformat()
            self._counters_reset_at_perf = perf_counter()


def _status_class_bucket(status_code: int) -> str:
    leading_digit = int(status_code) // 100
    if leading_digit == 1:
        return "1xx"
    if leading_digit == 2:
        return "2xx"
    if leading_digit == 3:
        return "3xx"
    if leading_digit == 4:
        return "4xx"
    return "5xx"


def _nearest_rank_percentile(sorted_samples: list[int], percentile: float) -> int:
    if not sorted_samples:
        return 0

    bounded_percentile = min(1.0, max(0.0, float(percentile)))
    rank = max(1, ceil(bounded_percentile * len(sorted_samples)))
    index = min(len(sorted_samples) - 1, rank - 1)
    return int(sorted_samples[index])


def _prune_request_timestamps(
    *, timestamps: deque[float], now: float, window_seconds: int
) -> None:
    threshold = float(now) - max(1, int(window_seconds))
    while timestamps and timestamps[0] < threshold:
        timestamps.popleft()


def _top_error_codes(
    *, error_codes: dict[str, int], total_errors: int, limit: int
) -> list[dict]:
    if total_errors <= 0:
        return []

    top_entries = sorted(
        error_codes.items(),
        key=lambda item: (-int(item[1]), item[0]),
    )[: max(1, int(limit))]
    return [
        {
            "code": code,
            "count": int(count),
            "ratio": round(int(count) / float(total_errors), 4),
        }
        for code, count in top_entries
    ]


def _build_consistency_stamp(*, runtime_started_at_utc: str, reset_at_utc: str) -> dict:
    runtime_started = str(runtime_started_at_utc).strip()
    reset_at = str(reset_at_utc).strip()
    return {
        "stamp": f"{runtime_started}|{reset_at}",
        "runtime_started_at_utc": runtime_started,
        "metrics_reset_at_utc": reset_at,
    }
