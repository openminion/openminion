from openminion.modules.task.scheduling.coordination import (
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
        )["state"]
        == "unreachable"
    )
    assert (
        scheduler_readiness_from_health(
            payload,
            reachable=True,
            identity_matches=False,
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
