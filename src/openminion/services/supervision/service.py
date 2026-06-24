from dataclasses import replace
from datetime import datetime, timedelta, timezone

from openminion.modules.telemetry.lifecycle import (
    build_lifecycle_telemetry_event,
    component_identity_key,
)
from openminion.modules.telemetry.schemas import TelemetryEvent

from .models import (
    BackoffState,
    RestartDecision,
    SupervisionDecision,
    SupervisionObservation,
    SupervisionPolicy,
)


class SupervisionService:
    """Host-local watchdog policy evaluator."""

    def evaluate(
        self,
        *,
        observation: SupervisionObservation,
        policy: SupervisionPolicy,
        observed_at: datetime | None = None,
        backoff_state: BackoffState | None = None,
    ) -> SupervisionDecision:
        now = observed_at or datetime.now(tz=timezone.utc)
        state = backoff_state or BackoffState()
        decision = SupervisionDecision(
            component=observation.component,
            component_key=component_identity_key(observation.component),
            posture="healthy",
            reason="lifecycle_healthy",
            alert_level="none",
            latest_event_type=observation.latest_event_type,
            restart_attempts=state.restart_attempts,
            consecutive_failures=state.consecutive_failures,
            last_heartbeat_at=observation.last_heartbeat_at,
            last_exit_reason=observation.last_exit_reason,
        )

        event_type = str(observation.latest_event_type or "").strip()
        stale_seconds = self._stale_heartbeat_seconds(
            observation=observation,
            observed_at=now,
        )
        if stale_seconds is not None:
            if (
                policy.stale_heartbeat_fail_after_seconds is not None
                and stale_seconds >= policy.stale_heartbeat_fail_after_seconds
            ):
                return replace(
                    decision,
                    posture="failed",
                    reason="stale_heartbeat_failed",
                    alert_level="critical",
                    stale_heartbeat_seconds=stale_seconds,
                )
            if (
                policy.stale_heartbeat_warn_after_seconds is not None
                and stale_seconds >= policy.stale_heartbeat_warn_after_seconds
            ):
                return replace(
                    decision,
                    posture="degraded",
                    reason="stale_heartbeat_degraded",
                    alert_level="warn",
                    stale_heartbeat_seconds=stale_seconds,
                )
        lag_seconds = self._scheduler_lag_seconds(observation)
        if lag_seconds is not None:
            if (
                policy.scheduler_lag_fail_after_seconds is not None
                and lag_seconds >= policy.scheduler_lag_fail_after_seconds
            ):
                return replace(
                    decision,
                    posture="failed",
                    reason="scheduler_lag_failed",
                    alert_level="critical",
                )
            if (
                policy.scheduler_lag_warn_after_seconds is not None
                and lag_seconds >= policy.scheduler_lag_warn_after_seconds
            ):
                return replace(
                    decision,
                    posture="degraded",
                    reason="scheduler_lag_degraded",
                    alert_level="warn",
                )

        if event_type in {
            "component.heartbeat",
            "component.recovered",
            "component.started",
        }:
            return decision

        if event_type in {"component.crashed", "component.restart_failed"}:
            restart_decision = self._restart_decision(
                policy=policy,
                backoff_state=state,
                observed_at=now,
            )
            return replace(
                decision,
                posture="failed",
                reason="component_crashed",
                alert_level="critical",
                restart_attempts=restart_decision.attempt,
                consecutive_failures=max(1, state.consecutive_failures + 1),
                restart=restart_decision,
            )

        if event_type in {"component.degraded", "component.stopped"}:
            restart_decision = self._restart_decision(
                policy=policy,
                backoff_state=state,
                observed_at=now,
            )
            return replace(
                decision,
                posture="degraded",
                reason="component_degraded",
                alert_level="warn",
                restart_attempts=restart_decision.attempt,
                consecutive_failures=max(0, state.consecutive_failures),
                restart=restart_decision,
            )

        return replace(
            decision,
            posture="unknown",
            reason="insufficient_supervision_signal",
            alert_level="warn",
        )

    def _restart_decision(
        self,
        *,
        policy: SupervisionPolicy,
        backoff_state: BackoffState,
        observed_at: datetime,
    ) -> RestartDecision:
        attempt = backoff_state.restart_attempts + 1
        if not policy.restart_enabled:
            return RestartDecision(
                action="disabled",
                reason="restart_disabled",
                attempt=attempt,
            )
        if policy.restart_max_attempts and attempt > policy.restart_max_attempts:
            return RestartDecision(
                action="suppressed",
                reason="restart_attempt_limit_reached",
                attempt=attempt,
            )
        if backoff_state.consecutive_failures >= policy.crash_loop_threshold:
            return RestartDecision(
                action="suppressed",
                reason="crash_loop_suppressed",
                attempt=attempt,
            )
        backoff_seconds = min(
            policy.restart_initial_backoff_seconds
            * (2 ** max(0, backoff_state.restart_attempts)),
            policy.restart_max_backoff_seconds,
        )
        if backoff_seconds <= 0:
            return RestartDecision(
                action="restart_now",
                reason="restart_immediate",
                attempt=attempt,
            )
        return RestartDecision(
            action="backoff",
            reason="restart_backoff",
            attempt=attempt,
            backoff_seconds=backoff_seconds,
            next_restart_at=(
                observed_at + timedelta(seconds=backoff_seconds)
            ).isoformat(),
        )

    def build_restart_lifecycle_event(
        self,
        *,
        decision: RestartDecision,
        component: dict[str, object],
        module_id: str,
        session_id: str,
        turn_id: str,
        supervision_decision: SupervisionDecision | None = None,
        source_classification: str = "native_canonical",
    ) -> TelemetryEvent | None:
        if decision.action == "none":
            return None
        event_type = "component.restart_requested"
        status = "warn"
        if decision.action in {"disabled", "suppressed"}:
            event_type = "component.restart_failed"
            status = "error"
        metrics: dict[str, object] = {"attempt": decision.attempt}
        if decision.backoff_seconds is not None:
            metrics["backoff_seconds"] = decision.backoff_seconds
        if supervision_decision is not None:
            metrics["restart_attempts"] = supervision_decision.restart_attempts
            metrics["consecutive_failures"] = supervision_decision.consecutive_failures
        evidence: dict[str, object] = {}
        if decision.next_restart_at:
            evidence["next_restart_at"] = decision.next_restart_at
        if supervision_decision is not None and supervision_decision.last_exit_reason:
            evidence["last_exit_reason"] = supervision_decision.last_exit_reason[:200]
        return build_lifecycle_telemetry_event(
            event_type=event_type,
            component=component,
            module_id=module_id,
            session_id=session_id,
            turn_id=turn_id,
            status=status,
            reason=decision.reason,
            metrics=metrics,
            evidence=evidence or None,
            source_classification=source_classification,
        )

    @staticmethod
    def _stale_heartbeat_seconds(
        *,
        observation: SupervisionObservation,
        observed_at: datetime,
    ) -> float | None:
        heartbeat_at = SupervisionService._parse_timestamp(
            observation.last_heartbeat_at
        )
        if heartbeat_at is None:
            return None
        return max(0.0, (observed_at - heartbeat_at).total_seconds())

    @staticmethod
    def _parse_timestamp(value: str | None) -> datetime | None:
        normalized = str(value or "").strip()
        if not normalized:
            return None
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _scheduler_lag_seconds(observation: SupervisionObservation) -> float | None:
        raw_lag = observation.metrics.get("lag_seconds")
        if raw_lag is None:
            return None
        try:
            return max(0.0, float(raw_lag))
        except (TypeError, ValueError):
            return None
