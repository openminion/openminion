from datetime import datetime, timezone

import pytest

from openminion.modules.task.scheduling.coordination import (
    ScheduleIntervalTooShortError,
    schedule_user_task,
    scheduler_readiness_from_health,
)


def test_scheduler_readiness_uses_daemon_health_component() -> None:
    payload = {
        "normalized_health_snapshot": {
            "components": [
                {
                    "component": {"component_kind": "cron_scheduler"},
                    "readiness": "ready",
                    "last_heartbeat_at": "2026-09-06T00:00:00+00:00",
                }
            ]
        }
    }

    assert scheduler_readiness_from_health(
        payload,
        reachable=True,
        identity_matches=True,
        now=datetime(2026, 9, 6, 0, 0, 20, tzinfo=timezone.utc),
    ) == {
        "state": "ready",
        "hosted_by": "daemon",
        "last_heartbeat_at": "2026-09-06T00:00:00+00:00",
        "reason": None,
    }
    assert (
        scheduler_readiness_from_health(
            payload,
            reachable=False,
            identity_matches=True,
            now=datetime(2026, 9, 6, 0, 0, 20, tzinfo=timezone.utc),
        )["state"]
        == "unreachable"
    )
    assert (
        scheduler_readiness_from_health(
            payload,
            reachable=True,
            identity_matches=False,
            now=datetime(2026, 9, 6, 0, 0, 20, tzinfo=timezone.utc),
        )["state"]
        == "degraded"
    )


def test_scheduler_readiness_requires_identity_and_heartbeat() -> None:
    no_heartbeat = {
        "normalized_health_snapshot": {
            "components": [
                {
                    "component": {"component_kind": "cron_scheduler"},
                    "readiness": "ready",
                }
            ]
        }
    }

    assert (
        scheduler_readiness_from_health(
            no_heartbeat,
            reachable=True,
            identity_matches=True,
        )["state"]
        == "unknown"
    )
    assert (
        scheduler_readiness_from_health(
            {},
            reachable=True,
            identity_matches=True,
        )["state"]
        == "unknown"
    )
    assert (
        scheduler_readiness_from_health(
            {"normalized_health_snapshot": {"components": []}},
            reachable=True,
            identity_matches=True,
        )["state"]
        == "degraded"
    )


def test_scheduler_readiness_rejects_stale_heartbeat() -> None:
    payload = {
        "normalized_health_snapshot": {
            "components": [
                {
                    "component": {"component_kind": "cron_scheduler"},
                    "readiness": "ready",
                    "last_heartbeat_at": "2026-09-06T00:00:00+00:00",
                }
            ]
        }
    }

    result = scheduler_readiness_from_health(
        payload,
        reachable=True,
        identity_matches=True,
        now=datetime(2026, 9, 6, 0, 0, 31, tzinfo=timezone.utc),
        stale_after_seconds=30,
    )

    assert result["state"] == "degraded"
    assert result["reason"] == "scheduler_heartbeat_stale"


def test_scheduler_readiness_rejects_future_heartbeat() -> None:
    payload = {
        "normalized_health_snapshot": {
            "components": [
                {
                    "component": {"component_kind": "cron_scheduler"},
                    "readiness": "ready",
                    "last_heartbeat_at": "2026-09-06T00:01:00+00:00",
                }
            ]
        }
    }

    result = scheduler_readiness_from_health(
        payload,
        reachable=True,
        identity_matches=True,
        now=datetime(2026, 9, 6, 0, 0, 0, tzinfo=timezone.utc),
    )

    assert result["state"] == "degraded"
    assert result["reason"] == "scheduler_heartbeat_in_future"


def test_scheduler_readiness_handles_malformed_components() -> None:
    result = scheduler_readiness_from_health(
        {
            "normalized_health_snapshot": {
                "components": [None, "bad", {"component": []}]
            }
        },
        reachable=True,
        identity_matches=True,
    )

    assert result["state"] == "unknown"
    assert result["reason"] == "health_components_invalid"


def test_schedule_user_task_normalizes_shared_creation_fields() -> None:
    captured = {}

    class _Owner:
        def schedule_user_task(self, **kwargs):
            captured.update(kwargs)
            return {"created": True}

    result = schedule_user_task(
        _Owner(),
        instruction="  inspect the repository  ",
        schedule={"kind": "every", "every_ms": 60_000},
        agent_id=" agent-a ",
        name="  Daily   review  ",
        origin={"channel": " api ", "session_id": " "},
    )

    assert result == {"created": True}
    assert captured == {
        "name": "Daily review",
        "instruction": "inspect the repository",
        "schedule": {"kind": "every", "every_ms": 60_000, "jitter_ms": 0},
        "agent_id": "agent-a",
        "origin": {"channel": "api"},
    }


def test_schedule_user_task_enforces_shared_cadence_floor() -> None:
    class _Owner:
        def schedule_user_task(self, **_kwargs):
            raise AssertionError("owner must not run")

    with pytest.raises(ScheduleIntervalTooShortError) as excinfo:
        schedule_user_task(
            _Owner(),
            instruction="work",
            schedule={"kind": "every", "every_ms": 9_999},
            agent_id="agent-a",
        )

    assert excinfo.value.every_ms == 9_999
