from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SupervisionPolicy:
    stale_heartbeat_warn_after_seconds: float | None = None
    stale_heartbeat_fail_after_seconds: float | None = None
    scheduler_lag_warn_after_seconds: float | None = None
    scheduler_lag_fail_after_seconds: float | None = None
    restart_enabled: bool = False
    restart_max_attempts: int = 0
    restart_initial_backoff_seconds: float = 5.0
    restart_max_backoff_seconds: float = 300.0
    crash_loop_window_seconds: float = 300.0
    crash_loop_threshold: int = 3
    clear_failure_on_recovery: bool = True

    def __post_init__(self) -> None:
        warn_after = self._clean_optional_seconds(
            self.stale_heartbeat_warn_after_seconds
        )
        fail_after = self._clean_optional_seconds(
            self.stale_heartbeat_fail_after_seconds
        )
        if (
            warn_after is not None
            and fail_after is not None
            and fail_after < warn_after
        ):
            raise ValueError("stale heartbeat fail threshold must be >= warn threshold")
        lag_warn_after = self._clean_optional_seconds(
            self.scheduler_lag_warn_after_seconds
        )
        lag_fail_after = self._clean_optional_seconds(
            self.scheduler_lag_fail_after_seconds
        )
        if (
            lag_warn_after is not None
            and lag_fail_after is not None
            and lag_fail_after < lag_warn_after
        ):
            raise ValueError("scheduler lag fail threshold must be >= warn threshold")
        object.__setattr__(self, "stale_heartbeat_warn_after_seconds", warn_after)
        object.__setattr__(self, "stale_heartbeat_fail_after_seconds", fail_after)
        object.__setattr__(self, "scheduler_lag_warn_after_seconds", lag_warn_after)
        object.__setattr__(self, "scheduler_lag_fail_after_seconds", lag_fail_after)
        object.__setattr__(
            self, "restart_max_attempts", max(0, int(self.restart_max_attempts))
        )
        object.__setattr__(
            self,
            "restart_initial_backoff_seconds",
            max(0.0, float(self.restart_initial_backoff_seconds)),
        )
        object.__setattr__(
            self,
            "restart_max_backoff_seconds",
            max(
                float(self.restart_initial_backoff_seconds),
                float(self.restart_max_backoff_seconds),
            ),
        )
        object.__setattr__(
            self,
            "crash_loop_window_seconds",
            max(1.0, float(self.crash_loop_window_seconds)),
        )
        object.__setattr__(
            self,
            "crash_loop_threshold",
            max(1, int(self.crash_loop_threshold)),
        )

    @staticmethod
    def _clean_optional_seconds(value: float | None) -> float | None:
        if value is None:
            return None
        return max(0.0, float(value))


@dataclass(frozen=True)
class SupervisionObservation:
    component: dict[str, Any]
    latest_event_type: str
    latest_observed_at: str | None = None
    last_heartbeat_at: str | None = None
    last_exit_reason: str | None = None
    source_classification: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BackoffState:
    restart_attempts: int = 0
    consecutive_failures: int = 0
    last_failure_at: str | None = None
    last_restart_at: str | None = None
    next_restart_at: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "restart_attempts", max(0, int(self.restart_attempts)))
        object.__setattr__(
            self,
            "consecutive_failures",
            max(0, int(self.consecutive_failures)),
        )


@dataclass(frozen=True)
class RestartDecision:
    action: str
    reason: str
    attempt: int = 0
    backoff_seconds: float | None = None
    next_restart_at: str | None = None


@dataclass(frozen=True)
class SupervisionDecision:
    component: dict[str, Any]
    component_key: str
    posture: str
    reason: str
    alert_level: str
    latest_event_type: str
    restart_attempts: int = 0
    consecutive_failures: int = 0
    stale_heartbeat_seconds: float | None = None
    last_heartbeat_at: str | None = None
    last_exit_reason: str | None = None
    restart: RestartDecision = field(
        default_factory=lambda: RestartDecision(
            action="none",
            reason="no_restart_required",
        )
    )
