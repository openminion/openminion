from __future__ import annotations

from types import SimpleNamespace

from openminion.api.core.deps import v1_daemon_health


class _HealthySubsystem:
    def healthcheck(self):
        return {"ok": True, "detail": "ready"}


class _FailingSubsystem:
    def healthcheck(self):
        raise RuntimeError("offline")


def test_v1_daemon_health_reports_subsystem_statuses() -> None:
    runtime = SimpleNamespace(
        config_path="/tmp/openminion.json",
        runtime_manager=SimpleNamespace(list_agents=lambda: ["a", "b"]),
        record_store=_HealthySubsystem(),
        memory_api=_FailingSubsystem(),
        provider=SimpleNamespace(name="provider"),
    )

    health = v1_daemon_health(runtime)

    assert health["available"] is True
    assert health["agents_hot"] == 2
    subsystems = health["subsystems"]
    assert subsystems["storage"]["status"] == "ok"
    assert subsystems["memory"]["status"] == "unavailable"
    assert subsystems["provider"]["status"] == "ok"


def test_v1_daemon_health_includes_subsystems_when_manager_missing() -> None:
    runtime = SimpleNamespace(
        config_path="/tmp/openminion.json",
        runtime_manager=None,
        record_store=_HealthySubsystem(),
    )

    health = v1_daemon_health(runtime)

    assert health["available"] is False
    assert health["subsystems"]["storage"]["status"] == "ok"
