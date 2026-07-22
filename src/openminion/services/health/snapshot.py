from typing import Any

from .lifecycle import (
    _build_supervision_component_snapshots,
    _lifecycle_component_key,
    _merge_lifecycle_health_state,
    _status_to_health_state,
)
from .reporting import _check_group
from .types import HealthCheck, LifecycleFact


def _build_normalized_health_snapshot(
    *,
    checks: list[HealthCheck],
    provider_name: str,
    default_channel: str,
    storage_path: str,
    observed_at: str,
    lifecycle_facts: dict[str, LifecycleFact],
) -> dict[str, Any]:
    grouped_checks: dict[str, list[HealthCheck]] = {}
    for check in checks:
        group = _check_group(check.id)
        grouped_checks.setdefault(group, []).append(check)

    components = []
    for group in ("config", "storage", "provider", "channels", "plugins", "runtime"):
        group_checks = grouped_checks.get(group, [])
        if not group_checks:
            continue
        components.append(
            _build_group_snapshot(
                group=group,
                group_checks=group_checks,
                provider_name=provider_name,
                default_channel=default_channel,
                storage_path=storage_path,
                observed_at=observed_at,
                lifecycle_facts=lifecycle_facts,
            )
        )
    components.extend(
        _build_supervision_component_snapshots(
            observed_at=observed_at,
            lifecycle_facts=lifecycle_facts,
        )
    )

    summary_state = _rollup_health_state(
        [str(item.get("health_state", "unknown")) for item in components]
    )
    return {
        "contract": "observability-health-snapshot-v1",
        "scope": "system",
        "observed_at": observed_at,
        "components": components,
        "summary": {
            "health_state": summary_state,
            "component_count": len(components),
        },
    }


def _merge_health_counts_with_supervision_components(
    *,
    ok_count: int,
    warn_count: int,
    fail_count: int,
    normalized_health_snapshot: dict[str, Any],
) -> tuple[int, int, int]:
    merged_ok = int(ok_count)
    merged_warn = int(warn_count)
    merged_fail = int(fail_count)
    components = list((normalized_health_snapshot.get("components") or []))
    for component in components:
        related_checks = component.get("related_checks")
        if related_checks:
            continue
        health_state = str(component.get("health_state") or "").strip().lower()
        if health_state == "failed":
            merged_fail += 1
        elif health_state == "degraded":
            merged_warn += 1
        elif health_state == "healthy":
            merged_ok += 1
    return merged_ok, merged_warn, merged_fail


def _build_group_snapshot(
    *,
    group: str,
    group_checks: list[HealthCheck],
    provider_name: str,
    default_channel: str,
    storage_path: str,
    observed_at: str,
    lifecycle_facts: dict[str, LifecycleFact],
) -> dict[str, Any]:
    component = _component_identity_for_group(
        group=group,
        provider_name=provider_name,
        default_channel=default_channel,
        storage_path=storage_path,
    )
    lifecycle_fact = lifecycle_facts.get(_lifecycle_component_key(component))
    status = _aggregate_check_status(group_checks)
    state = _status_to_health_state(status)
    state = _merge_lifecycle_health_state(state, lifecycle_fact)
    last_error = None
    for check in group_checks:
        if check.status in {"fail", "warn"}:
            last_error = check.message
            break
    if last_error is None and lifecycle_fact is not None:
        last_error = lifecycle_fact.last_exit_reason

    status_message = "; ".join(
        str(check.message) for check in group_checks if str(check.message).strip()
    )
    if lifecycle_fact is not None and lifecycle_fact.latest_event_type:
        lifecycle_note = (
            f"lifecycle={lifecycle_fact.latest_event_type}"
            if not lifecycle_fact.last_exit_reason
            else f"lifecycle={lifecycle_fact.latest_event_type} reason={lifecycle_fact.last_exit_reason}"
        )
        status_message = (
            f"{status_message}; {lifecycle_note}"[:400]
            if status_message
            else lifecycle_note[:400]
        )

    snapshot = {
        "component": component,
        "liveness": state["liveness"],
        "readiness": state["readiness"],
        "health_state": state["health_state"],
        "status_message": status_message[:400],
        "last_error": last_error,
        "observed_at": observed_at,
        "related_checks": [check.id for check in group_checks],
    }
    if lifecycle_fact is not None:
        snapshot["lifecycle_event_type"] = lifecycle_fact.latest_event_type
        snapshot["lifecycle_observed_at"] = lifecycle_fact.latest_observed_at
        if lifecycle_fact.source_classification:
            snapshot["lifecycle_source_classification"] = (
                lifecycle_fact.source_classification
            )
        if lifecycle_fact.last_heartbeat_at:
            snapshot["last_heartbeat_at"] = lifecycle_fact.last_heartbeat_at
        if lifecycle_fact.last_exit_reason:
            snapshot["last_exit_reason"] = lifecycle_fact.last_exit_reason
    return snapshot


def _component_identity_for_group(
    *,
    group: str,
    provider_name: str,
    default_channel: str,
    storage_path: str,
) -> dict[str, Any]:
    if group == "config":
        return {
            "component_kind": "runtime_manager",
            "component_id": "config-loader",
            "scope": "system",
            "owner_module": "openminion-runtime",
            "labels": {"surface": "config"},
        }
    if group == "storage":
        return {
            "component_kind": "storage_backend",
            "component_id": "sqlite-main",
            "scope": "system",
            "owner_module": "openminion-storage",
            "labels": {"path": storage_path},
        }
    if group == "provider":
        return {
            "component_kind": "provider_binding",
            "component_id": provider_name or "primary",
            "scope": "system",
            "owner_module": "openminion-llm",
            "labels": {"provider": provider_name or "unknown"},
        }
    if group == "channels":
        return {
            "component_kind": "channel_adapter",
            "component_id": default_channel or "default",
            "scope": "system",
            "owner_module": "openminion-controlplane",
            "labels": {"default_channel": default_channel or ""},
        }
    if group == "plugins":
        return {
            "component_kind": "tool_runtime",
            "component_id": "plugin-registry",
            "scope": "system",
            "owner_module": "openminion-tool",
            "labels": {"surface": "plugins"},
        }
    return {
        "component_kind": "runtime_manager",
        "component_id": "primary",
        "scope": "system",
        "owner_module": "openminion-runtime",
        "labels": {"surface": group},
    }


def _aggregate_check_status(checks: list[HealthCheck]) -> str:
    if any(check.status == "fail" for check in checks):
        return "fail"
    if any(check.status == "warn" for check in checks):
        return "warn"
    if any(check.status == "ok" for check in checks):
        return "ok"
    return "unknown"


def _rollup_health_state(states: list[str]) -> str:
    if any(state == "failed" for state in states):
        return "failed"
    if any(state == "degraded" for state in states):
        return "degraded"
    if states and all(state == "healthy" for state in states):
        return "healthy"
    return "unknown"
