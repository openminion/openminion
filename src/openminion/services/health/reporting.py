import logging
from time import perf_counter
from typing import Any, Dict, List

from openminion.api.constants import API_METRICS_TOKEN_ENV, API_METRICS_TOKEN_HEADER
from openminion.base.config import EnvironmentConfig
from .types import HealthCheck

_HEALTH_LOG = logging.getLogger("openminion.health")


def _build_readiness_by_group(checks: List[HealthCheck]) -> Dict[str, Dict[str, int]]:
    grouped: Dict[str, Dict[str, int]] = {}
    for check in checks:
        group = _check_group(check.id)
        bucket = grouped.setdefault(group, {"ok": 0, "warn": 0, "fail": 0, "total": 0})
        status = check.status if check.status in {"ok", "warn", "fail"} else "warn"
        bucket[status] += 1
        bucket["total"] += 1
    return dict(sorted(grouped.items()))


def _check_group(check_id: str) -> str:
    normalized = check_id.strip().lower()
    if normalized.startswith("config."):
        return "config"
    if normalized.startswith("storage."):
        return "storage"
    if normalized.startswith("provider."):
        return "provider"
    if normalized.startswith("channels."):
        return "channels"
    if normalized.startswith("plugins."):
        return "plugins"
    if normalized.startswith("runtime."):
        return "runtime"
    return "other"


def _emit_normalization_telemetry(
    *,
    checks: List[HealthCheck],
    normalized_health_snapshot: Dict[str, Any],
    fail_count: int,
    warn_count: int,
) -> None:
    components = list(normalized_health_snapshot.get("components", []))
    _HEALTH_LOG.info(
        "health.normalization.summary checks=%d fail=%d warn=%d components=%d",
        len(checks),
        int(fail_count),
        int(warn_count),
        len(components),
    )
    if fail_count > 0:
        failed_check_ids = [check.id for check in checks if check.status == "fail"]
        failed_components = [
            {
                "component_kind": item.get("component", {}).get("component_kind", ""),
                "component_id": item.get("component", {}).get("component_id", ""),
                "health_state": item.get("health_state", ""),
            }
            for item in components
            if str(item.get("health_state", "")).strip().lower() == "failed"
        ]
        _HEALTH_LOG.warning(
            "health.normalization.negative failed_checks=%s failed_components=%s",
            failed_check_ids[:20],
            failed_components[:20],
        )


def _duration_ms(started_at: float) -> int:
    return max(0, int((perf_counter() - started_at) * 1000))


def _build_dependency_timing_summary(
    checks: List[HealthCheck], *, started_at: float
) -> Dict[str, Any]:
    entries = []
    for check in checks:
        if check.duration_ms is None:
            continue
        entries.append((check.id, max(0, int(check.duration_ms))))

    total_duration = _duration_ms(started_at)
    if not entries:
        return {
            "total": total_duration,
            "count": 0,
            "avg": 0,
            "max": 0,
            "slowest_check_id": None,
            "by_group": {},
        }

    max_entry = max(entries, key=lambda item: item[1])
    duration_sum = sum(item[1] for item in entries)
    by_group: Dict[str, Dict[str, int]] = {}
    for check_id, duration in entries:
        group = _check_group(check_id)
        bucket = by_group.setdefault(
            group, {"count": 0, "total": 0, "max": 0, "avg": 0}
        )
        bucket["count"] += 1
        bucket["total"] += duration
        bucket["max"] = max(bucket["max"], duration)

    for bucket in by_group.values():
        count = bucket["count"]
        bucket["avg"] = int(bucket["total"] / count) if count else 0

    return {
        "total": total_duration,
        "count": len(entries),
        "avg": int(duration_sum / len(entries)),
        "max": max_entry[1],
        "slowest_check_id": max_entry[0],
        "by_group": dict(sorted(by_group.items())),
    }


def _build_operator_hints(env_config: EnvironmentConfig) -> Dict[str, Any]:
    metrics_token_required = env_config.has(API_METRICS_TOKEN_ENV)
    return {
        "metrics": {
            "path": "/metrics",
            "reset_example": "/metrics?reset=true",
            "requires_token": metrics_token_required,
            "token_header": API_METRICS_TOKEN_HEADER,
            "token_env": API_METRICS_TOKEN_ENV,
        }
    }


def _normalize_metrics_consistency(
    metrics_consistency: Dict[str, Any],
) -> Dict[str, str]:
    stamp = str(metrics_consistency.get("stamp", "")).strip()
    runtime_started = str(metrics_consistency.get("runtime_started_at_utc", "")).strip()
    metrics_reset = str(metrics_consistency.get("metrics_reset_at_utc", "")).strip()
    return {
        "stamp": stamp,
        "runtime_started_at_utc": runtime_started,
        "metrics_reset_at_utc": metrics_reset,
    }


def _resolve_health_latency_budget_threshold_ms(env_config: EnvironmentConfig) -> int:
    return max(0, env_config.get_int("OPENMINION_HEALTH_CHECK_WARN_MS", 250))


def _build_latency_budget_check(
    *, checks: List[HealthCheck], threshold_ms: int
) -> HealthCheck:
    timed_checks: list[dict[str, int | str]] = [
        {"id": check.id, "duration_ms": int(check.duration_ms)}
        for check in checks
        if check.duration_ms is not None
    ]
    slow_checks = [
        item for item in timed_checks if int(item["duration_ms"]) >= threshold_ms
    ]
    slow_checks.sort(key=lambda item: int(item["duration_ms"]), reverse=True)

    if not slow_checks:
        return HealthCheck(
            id="runtime.dependency_latency_budget",
            status="ok",
            message="All dependency checks are within the latency budget.",
            details={
                "threshold_ms": threshold_ms,
                "slow_check_count": 0,
                "slow_checks": [],
            },
        )

    return HealthCheck(
        id="runtime.dependency_latency_budget",
        status="warn",
        message=f"{len(slow_checks)} dependency checks exceeded the latency budget.",
        details={
            "threshold_ms": threshold_ms,
            "slow_check_count": len(slow_checks),
            "slow_checks": slow_checks,
        },
    )
