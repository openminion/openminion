from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from openminion.services.cron.scheduler import build_cron_supervision_policy
from openminion.modules.telemetry.lifecycle import (
    build_component_identity,
    build_cron_scheduler_component_identity,
)
from openminion.services.supervision import (
    BackoffState,
    SupervisionObservation,
    SupervisionPolicy,
    SupervisionService,
)


def _runtime_manager_component() -> dict[str, str]:
    return build_component_identity(
        component_kind="runtime_manager",
        component_id="primary",
        scope="system",
        owner_module="openminion-runtime",
    )


def _cron_scheduler_component() -> dict[str, str]:
    return build_cron_scheduler_component_identity(daemon_component_id="primary")


def test_supervision_policy_rejects_fail_threshold_below_warn_threshold() -> None:
    with pytest.raises(ValueError, match="fail threshold"):
        SupervisionPolicy(
            stale_heartbeat_warn_after_seconds=60,
            stale_heartbeat_fail_after_seconds=30,
        )


def test_supervision_policy_rejects_scheduler_lag_fail_threshold_below_warn_threshold() -> (
    None
):
    with pytest.raises(ValueError, match="scheduler lag fail threshold"):
        SupervisionPolicy(
            scheduler_lag_warn_after_seconds=60,
            scheduler_lag_fail_after_seconds=30,
        )


def test_supervision_service_treats_recent_heartbeat_as_healthy() -> None:
    service = SupervisionService()
    now = datetime(2026, 3, 19, 6, 0, tzinfo=timezone.utc)
    decision = service.evaluate(
        observation=SupervisionObservation(
            component=_runtime_manager_component(),
            latest_event_type="component.heartbeat",
            latest_observed_at=now.isoformat(),
            last_heartbeat_at=now.isoformat(),
        ),
        policy=SupervisionPolicy(
            stale_heartbeat_warn_after_seconds=30,
            stale_heartbeat_fail_after_seconds=120,
        ),
        observed_at=now,
    )

    assert decision.component_key == "runtime_manager:primary:system"
    assert decision.posture == "healthy"
    assert decision.restart.action == "none"


def test_supervision_service_marks_stale_heartbeat_degraded_then_failed() -> None:
    service = SupervisionService()
    now = datetime(2026, 3, 19, 6, 0, tzinfo=timezone.utc)
    heartbeat_at = (now - timedelta(seconds=45)).isoformat()
    observation = SupervisionObservation(
        component=_runtime_manager_component(),
        latest_event_type="component.started",
        latest_observed_at=heartbeat_at,
        last_heartbeat_at=heartbeat_at,
    )
    policy = SupervisionPolicy(
        stale_heartbeat_warn_after_seconds=30,
        stale_heartbeat_fail_after_seconds=60,
    )

    degraded = service.evaluate(
        observation=observation,
        policy=policy,
        observed_at=now,
    )
    failed = service.evaluate(
        observation=observation,
        policy=policy,
        observed_at=now + timedelta(seconds=30),
    )

    assert degraded.posture == "degraded"
    assert degraded.reason == "stale_heartbeat_degraded"
    assert degraded.alert_level == "warn"
    assert degraded.restart.action == "none"
    assert failed.posture == "failed"
    assert failed.reason == "stale_heartbeat_failed"
    assert failed.alert_level == "critical"
    assert failed.restart.action == "none"


def test_supervision_service_returns_explicit_non_recovery_when_restart_disabled() -> (
    None
):
    service = SupervisionService()
    now = datetime(2026, 3, 19, 6, 0, tzinfo=timezone.utc)
    decision = service.evaluate(
        observation=SupervisionObservation(
            component=_runtime_manager_component(),
            latest_event_type="component.crashed",
            latest_observed_at=now.isoformat(),
            last_exit_reason="kill_switch",
        ),
        policy=SupervisionPolicy(restart_enabled=False),
        observed_at=now,
    )

    assert decision.posture == "failed"
    assert decision.restart.action == "disabled"
    assert decision.restart.reason == "restart_disabled"
    assert decision.restart_attempts == 1
    assert decision.consecutive_failures == 1
    assert decision.last_exit_reason == "kill_switch"


def test_supervision_service_uses_backoff_for_restart_eligible_failure() -> None:
    service = SupervisionService()
    now = datetime(2026, 3, 19, 6, 0, tzinfo=timezone.utc)
    decision = service.evaluate(
        observation=SupervisionObservation(
            component=_runtime_manager_component(),
            latest_event_type="component.crashed",
            latest_observed_at=now.isoformat(),
        ),
        policy=SupervisionPolicy(
            restart_enabled=True,
            restart_max_attempts=3,
            restart_initial_backoff_seconds=5,
            restart_max_backoff_seconds=60,
            crash_loop_threshold=4,
        ),
        observed_at=now,
        backoff_state=BackoffState(restart_attempts=1, consecutive_failures=1),
    )

    assert decision.posture == "failed"
    assert decision.restart.action == "backoff"
    assert decision.restart.attempt == 2
    assert decision.restart.backoff_seconds == 10
    assert decision.restart.next_restart_at

    event = service.build_restart_lifecycle_event(
        decision=decision.restart,
        component=_runtime_manager_component(),
        module_id="openminion-runtime",
        session_id="lifecycle:runtime_manager:primary",
        turn_id="restart-requested-test",
        supervision_decision=decision,
    )

    assert event is not None
    assert event.event_type == "component.restart_requested"
    assert event.data["reason"] == "restart_backoff"
    assert event.data["metrics"]["attempt"] == 2
    assert event.data["metrics"]["backoff_seconds"] == 10
    assert event.data["metrics"]["restart_attempts"] == 2
    assert event.data["metrics"]["consecutive_failures"] == 2


def test_supervision_service_suppresses_crash_loop_restart() -> None:
    service = SupervisionService()
    now = datetime(2026, 3, 19, 6, 0, tzinfo=timezone.utc)
    decision = service.evaluate(
        observation=SupervisionObservation(
            component=_runtime_manager_component(),
            latest_event_type="component.crashed",
            latest_observed_at=now.isoformat(),
        ),
        policy=SupervisionPolicy(
            restart_enabled=True,
            restart_max_attempts=10,
            crash_loop_threshold=3,
        ),
        observed_at=now,
        backoff_state=BackoffState(restart_attempts=2, consecutive_failures=3),
    )

    assert decision.restart.action == "suppressed"
    assert decision.restart.reason == "crash_loop_suppressed"

    event = service.build_restart_lifecycle_event(
        decision=decision.restart,
        component=_runtime_manager_component(),
        module_id="openminion-runtime",
        session_id="lifecycle:runtime_manager:primary",
        turn_id="restart-failed-test",
        supervision_decision=decision,
    )

    assert event is not None
    assert event.event_type == "component.restart_failed"
    assert event.data["reason"] == "crash_loop_suppressed"
    assert event.data["metrics"]["restart_attempts"] == 3
    assert event.data["metrics"]["consecutive_failures"] == 4


def test_supervision_service_builds_restart_failed_event_for_disabled_policy() -> None:
    service = SupervisionService()
    decision = service.evaluate(
        observation=SupervisionObservation(
            component=_runtime_manager_component(),
            latest_event_type="component.crashed",
            latest_observed_at="2026-03-19T06:00:00+00:00",
            last_exit_reason="x" * 300,
        ),
        policy=SupervisionPolicy(restart_enabled=False),
        observed_at=datetime(2026, 3, 19, 6, 0, tzinfo=timezone.utc),
    )
    event = service.build_restart_lifecycle_event(
        decision=decision.restart,
        component=_runtime_manager_component(),
        module_id="openminion-runtime",
        session_id="lifecycle:runtime_manager:primary",
        turn_id="restart-disabled-test",
        supervision_decision=decision,
    )

    assert event is not None
    assert event.event_type == "component.restart_failed"
    assert event.data["reason"] == "restart_disabled"
    assert len(event.data["evidence"]["last_exit_reason"]) == 200


def test_supervision_service_treats_restart_failed_lifecycle_input_as_failure() -> None:
    service = SupervisionService()
    decision = service.evaluate(
        observation=SupervisionObservation(
            component=_runtime_manager_component(),
            latest_event_type="component.restart_failed",
            latest_observed_at="2026-03-19T06:00:00+00:00",
            last_exit_reason="restart_disabled",
        ),
        policy=SupervisionPolicy(restart_enabled=False),
        observed_at=datetime(2026, 3, 19, 6, 0, tzinfo=timezone.utc),
    )

    assert decision.posture == "failed"
    assert decision.reason == "component_crashed"
    assert decision.restart.action == "disabled"
    assert decision.last_exit_reason == "restart_disabled"


def test_supervision_service_returns_no_restart_event_when_restart_is_not_required() -> (
    None
):
    service = SupervisionService()
    decision = service.evaluate(
        observation=SupervisionObservation(
            component=_runtime_manager_component(),
            latest_event_type="component.heartbeat",
            latest_observed_at="2026-03-19T06:00:00+00:00",
            last_heartbeat_at="2026-03-19T06:00:00+00:00",
        ),
        policy=SupervisionPolicy(),
        observed_at=datetime(2026, 3, 19, 6, 0, tzinfo=timezone.utc),
    )

    event = service.build_restart_lifecycle_event(
        decision=decision.restart,
        component=_runtime_manager_component(),
        module_id="openminion-runtime",
        session_id="lifecycle:runtime_manager:primary",
        turn_id="no-restart-required-test",
        supervision_decision=decision,
    )

    assert event is None


def test_cron_supervision_policy_marks_recent_scheduler_heartbeat_healthy() -> None:
    service = SupervisionService()
    now = datetime(2026, 3, 19, 6, 0, tzinfo=timezone.utc)

    decision = service.evaluate(
        observation=SupervisionObservation(
            component=_cron_scheduler_component(),
            latest_event_type="component.heartbeat",
            latest_observed_at=now.isoformat(),
            last_heartbeat_at=now.isoformat(),
            metrics={"lag_seconds": 0.5, "tick_seconds": 2.0},
        ),
        policy=build_cron_supervision_policy(tick_seconds=2.0),
        observed_at=now,
    )

    assert decision.posture == "healthy"
    assert decision.reason == "lifecycle_healthy"


def test_cron_supervision_policy_marks_scheduler_lag_degraded_then_failed() -> None:
    service = SupervisionService()
    now = datetime(2026, 3, 19, 6, 0, tzinfo=timezone.utc)
    observation = SupervisionObservation(
        component=_cron_scheduler_component(),
        latest_event_type="component.heartbeat",
        latest_observed_at=now.isoformat(),
        last_heartbeat_at=now.isoformat(),
        metrics={"lag_seconds": 5.0, "tick_seconds": 2.0},
    )
    policy = build_cron_supervision_policy(
        tick_seconds=2.0,
        scheduler_lag_warn_after_seconds=4.0,
        scheduler_lag_fail_after_seconds=8.0,
    )

    degraded = service.evaluate(
        observation=observation,
        policy=policy,
        observed_at=now,
    )
    failed = service.evaluate(
        observation=SupervisionObservation(
            component=_cron_scheduler_component(),
            latest_event_type="component.heartbeat",
            latest_observed_at=now.isoformat(),
            last_heartbeat_at=now.isoformat(),
            metrics={"lag_seconds": 9.0, "tick_seconds": 2.0},
        ),
        policy=policy,
        observed_at=now,
    )

    assert degraded.posture == "degraded"
    assert degraded.reason == "scheduler_lag_degraded"
    assert failed.posture == "failed"
    assert failed.reason == "scheduler_lag_failed"
