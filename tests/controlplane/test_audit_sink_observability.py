from __future__ import annotations

import logging

from openminion.modules.controlplane.runtime.audit import AuditLogger


def test_audit_sink_failure_increments_counter_and_logs(caplog) -> None:
    def bad_sink(event: object) -> None:
        raise RuntimeError("disk full")

    logger = AuditLogger(sink=bad_sink)

    with caplog.at_level(logging.ERROR):
        logger.emit("controlplane.test", session_id="sess-1")

    assert logger.failure_count() == 1
    assert logger.health_status()["healthy"] is False
    assert "RuntimeError: disk full" == logger.health_status()["last_error"]
    assert logger.health_status()["wizard_step_failures"] == 0
    assert any(record.message == "audit.sink.failure" for record in caplog.records)


def test_audit_sink_healthy_status_reports_zero_failures() -> None:
    emitted: list[object] = []

    def good_sink(event: object) -> None:
        emitted.append(event)

    logger = AuditLogger(sink=good_sink)
    logger.emit("controlplane.test")

    assert len(emitted) == 1
    assert logger.failure_count() == 0
    assert logger.health_status() == {
        "healthy": True,
        "failures": 0,
        "last_error": None,
        "wizard_step_failures": 0,
    }


def test_wizard_step_failure_event_increments_health_counter() -> None:
    logger = AuditLogger()
    logger.emit("cp.wizard.step.failed", session_id="sess-1")

    assert logger.health_status()["wizard_step_failures"] == 1


def test_audit_sink_writes_stderr_on_100th_failure(capsys) -> None:
    def bad_sink(event: object) -> None:
        raise RuntimeError("disk full")

    logger = AuditLogger(sink=bad_sink)
    for idx in range(100):
        logger.emit(f"controlplane.test.{idx}")

    err = capsys.readouterr().err
    assert logger.failure_count() == 100
    assert "audit.sink.failure" in err
    assert "count=100" in err
