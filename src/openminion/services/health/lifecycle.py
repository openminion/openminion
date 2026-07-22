import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from collections.abc import Mapping

from openminion.modules.telemetry.service import resolve_telemetry_db_path
from .observability import (
    _safe_json_object,
    _sqlite_table_exists,
)
from .types import LifecycleFact
from openminion.services.supervision import (
    SupervisionDecision,
    SupervisionObservation,
    SupervisionPolicy,
    SupervisionService,
)


def _load_lifecycle_facts(
    *,
    home_root: Path | None,
) -> dict[str, LifecycleFact]:
    telemetry_path = resolve_telemetry_db_path(home_root=home_root).db_path
    if telemetry_path == ":memory:":
        return {}
    telemetry_db = Path(telemetry_path)
    if not telemetry_db.exists():
        return {}

    try:
        conn = sqlite3.connect(str(telemetry_db))
        conn.row_factory = sqlite3.Row
    except Exception:
        return {}

    try:
        if not _sqlite_table_exists(conn, "events"):
            return {}
        rows = conn.execute(
            """
            SELECT timestamp, event_type, data
            FROM events
            WHERE session_id LIKE 'lifecycle:%'
              AND event_type LIKE 'component.%'
            ORDER BY timestamp ASC
            """
        ).fetchall()
    except Exception:
        return {}
    finally:
        try:
            conn.close()
        except Exception:
            pass

    facts: dict[str, LifecycleFact] = {}
    for row in rows:
        payload = _safe_json_object(row["data"])
        component = payload.get("component")
        if not isinstance(component, Mapping):
            continue
        key = _lifecycle_component_key(component)
        if not key:
            continue
        event_timestamp = _timestamp_to_utc_iso(row["timestamp"])
        event_type = str(row["event_type"] or "").strip()
        source_classification = (
            str(payload.get("source_classification") or "").strip() or None
        )
        reason = str(payload.get("reason") or "").strip() or None
        metrics = payload.get("metrics")
        current = facts.get(key)
        if current is None:
            current = LifecycleFact(
                component=dict(component),
                latest_event_type=event_type,
                latest_observed_at=event_timestamp,
                source_classification=source_classification,
                metrics=dict(metrics) if isinstance(metrics, Mapping) else None,
            )
            facts[key] = current
        current.observe(
            component=component,
            event_type=event_type,
            observed_at=event_timestamp,
            source_classification=source_classification,
            reason=reason,
            metrics=metrics if isinstance(metrics, Mapping) else None,
        )
    return facts


def _build_supervision_component_snapshots(
    *,
    observed_at: str,
    lifecycle_facts: dict[str, LifecycleFact],
) -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = []
    for component, policy in _supervision_policies_for_lifecycle_facts(lifecycle_facts):
        key = _lifecycle_component_key(component)
        lifecycle_fact = lifecycle_facts.get(key)
        if lifecycle_fact is None:
            continue
        decision = _evaluate_supervision_decision(
            component=component,
            lifecycle_fact=lifecycle_fact,
            policy=policy,
            observed_at=observed_at,
        )
        if decision is None:
            continue
        components.append(
            _build_supervision_component_snapshot(
                component=component,
                lifecycle_fact=lifecycle_fact,
                decision=decision,
                observed_at=observed_at,
            )
        )
    return components


def _supervision_policies_for_lifecycle_facts(
    lifecycle_facts: dict[str, LifecycleFact],
) -> list[tuple[dict[str, Any], SupervisionPolicy]]:
    from openminion.daemon import build_daemon_supervision_policy
    from openminion.services.cron.scheduler import build_cron_supervision_policy
    from openminion.modules.telemetry.lifecycle import (
        build_component_identity,
        build_cron_scheduler_component_identity,
    )

    daemon_component = build_component_identity(
        component_kind="daemon",
        component_id="primary",
        scope="system",
        owner_module="openminion-runtime",
        capabilities=["http", "scheduler", "heartbeat"],
        labels={"topology": "daemon-hosted"},
    )
    cron_component = build_cron_scheduler_component_identity(
        daemon_component_id="primary",
    )
    cron_fact = lifecycle_facts.get(_lifecycle_component_key(cron_component))
    cron_metrics = dict(cron_fact.metrics or {}) if cron_fact is not None else {}
    tick_seconds = _as_float(cron_metrics.get("tick_seconds"), 2.0)
    return [
        (daemon_component, build_daemon_supervision_policy()),
        (cron_component, build_cron_supervision_policy(tick_seconds=tick_seconds)),
    ]


def _evaluate_supervision_decision(
    *,
    component: Mapping[str, Any],
    lifecycle_fact: LifecycleFact,
    policy: SupervisionPolicy,
    observed_at: str,
) -> SupervisionDecision | None:
    observed_dt = _parse_iso_datetime(observed_at)
    if observed_dt is None:
        return None
    return SupervisionService().evaluate(
        observation=SupervisionObservation(
            component=dict(component),
            latest_event_type=lifecycle_fact.latest_event_type,
            latest_observed_at=lifecycle_fact.latest_observed_at,
            last_heartbeat_at=lifecycle_fact.last_heartbeat_at,
            last_exit_reason=lifecycle_fact.last_exit_reason,
            source_classification=lifecycle_fact.source_classification,
            metrics=dict(lifecycle_fact.metrics or {}),
        ),
        policy=policy,
        observed_at=observed_dt,
    )


def _build_supervision_component_snapshot(
    *,
    component: Mapping[str, Any],
    lifecycle_fact: LifecycleFact,
    decision: SupervisionDecision,
    observed_at: str,
) -> dict[str, Any]:
    state = _supervision_posture_to_health_state(decision.posture)
    status_message = f"supervision={decision.reason}"
    if decision.restart.action != "none":
        status_message += (
            f"; restart={decision.restart.action}:{decision.restart.reason}"
        )
    snapshot: dict[str, Any] = {
        "component": dict(component),
        "liveness": state["liveness"],
        "readiness": state["readiness"],
        "health_state": state["health_state"],
        "status_message": status_message[:400],
        "last_error": decision.last_exit_reason,
        "observed_at": observed_at,
        "related_checks": [],
        "lifecycle_event_type": lifecycle_fact.latest_event_type,
        "lifecycle_observed_at": lifecycle_fact.latest_observed_at,
        "supervision": {
            "reason": decision.reason,
            "alert_level": decision.alert_level,
            "restart": {
                "action": decision.restart.action,
                "reason": decision.restart.reason,
                "attempt": decision.restart.attempt,
                "backoff_seconds": decision.restart.backoff_seconds,
                "next_restart_at": decision.restart.next_restart_at,
            },
            "restart_attempts": decision.restart_attempts,
            "consecutive_failures": decision.consecutive_failures,
        },
    }
    if lifecycle_fact.source_classification:
        snapshot["lifecycle_source_classification"] = (
            lifecycle_fact.source_classification
        )
    if lifecycle_fact.last_heartbeat_at:
        snapshot["last_heartbeat_at"] = lifecycle_fact.last_heartbeat_at
    if lifecycle_fact.last_exit_reason:
        snapshot["last_exit_reason"] = lifecycle_fact.last_exit_reason
    if decision.stale_heartbeat_seconds is not None:
        snapshot["supervision"]["stale_heartbeat_seconds"] = (
            decision.stale_heartbeat_seconds
        )
    return snapshot


def _supervision_posture_to_health_state(posture: str) -> dict[str, str]:
    normalized = str(posture or "").strip().lower()
    if normalized == "healthy":
        return {"liveness": "alive", "readiness": "ready", "health_state": "healthy"}
    if normalized == "degraded":
        return {
            "liveness": "alive",
            "readiness": "not_ready",
            "health_state": "degraded",
        }
    if normalized == "failed":
        return {
            "liveness": "unknown",
            "readiness": "not_ready",
            "health_state": "failed",
        }
    return {
        "liveness": "unknown",
        "readiness": "unknown",
        "health_state": "unknown",
    }


def _lifecycle_component_key(component: Mapping[str, Any]) -> str:
    component_kind = str(component.get("component_kind") or "").strip()
    component_id = str(component.get("component_id") or "").strip()
    scope = str(component.get("scope") or "").strip()
    if not component_kind or not component_id or not scope:
        return ""
    return f"{component_kind}:{component_id}:{scope}"


def _merge_lifecycle_health_state(
    state: dict[str, str],
    lifecycle_fact: LifecycleFact | None,
) -> dict[str, str]:
    if lifecycle_fact is None:
        return state

    merged = dict(state)
    event_type = lifecycle_fact.latest_event_type
    if event_type in {
        "component.started",
        "component.heartbeat",
        "component.recovered",
    }:
        merged["liveness"] = "alive"
        return merged
    if event_type == "component.crashed":
        merged["liveness"] = "unknown"
        if merged["readiness"] == "ready":
            merged["readiness"] = "not_ready"
        if merged["health_state"] == "healthy":
            merged["health_state"] = "degraded"
        return merged
    return merged


def _status_to_health_state(status: str) -> dict[str, str]:
    if status == "fail":
        return {
            # Phase 2 health normalization is based on readiness/config/probe posture,
            "liveness": "unknown",
            "readiness": "not_ready",
            "health_state": "failed",
        }
    if status == "warn":
        return {
            "liveness": "alive",
            "readiness": "not_ready",
            "health_state": "degraded",
        }
    if status == "ok":
        return {
            "liveness": "alive",
            "readiness": "ready",
            "health_state": "healthy",
        }
    return {
        "liveness": "unknown",
        "readiness": "unknown",
        "health_state": "unknown",
    }


def _timestamp_to_utc_iso(raw_timestamp: Any) -> str:
    try:
        timestamp_value = float(raw_timestamp)
    except (TypeError, ValueError):
        return datetime.now(tz=timezone.utc).isoformat()
    return datetime.fromtimestamp(timestamp_value, tz=timezone.utc).isoformat()


def _parse_iso_datetime(raw_value: str | None) -> datetime | None:
    normalized = str(raw_value or "").strip()
    if not normalized:
        return None
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _as_float(raw_value: Any, default: float) -> float:
    try:
        return float(raw_value)
    except (TypeError, ValueError):
        return float(default)
