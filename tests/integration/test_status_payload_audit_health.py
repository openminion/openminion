from __future__ import annotations

from tests.integration.test_unified_config_bootstrap import (
    _build_runtime,
    _close_runtime,
    _make_config,
)


def test_status_payload_includes_healthy_audit_health_block(tmp_path) -> None:
    config = _make_config(tmp_path, mode="polling")
    lifecycle, runtime = _build_runtime(config, tmp_path)
    try:
        payload = lifecycle.status_payload(runtime)
        assert payload["audit_health"] == {
            "audit": {
                "healthy": True,
                "failures": 0,
                "last_error": None,
            },
            "binding_warnings": 0,
            "wizard_step_failures": 0,
        }
    finally:
        _close_runtime(runtime)


def test_status_payload_reports_degraded_audit_health(tmp_path) -> None:
    config = _make_config(tmp_path, mode="polling")
    lifecycle, runtime = _build_runtime(config, tmp_path)
    try:
        runner = runtime.channels.get("telegram")
        audit_logger = runner._runtime.audit_logger

        def bad_sink(event: object) -> None:
            raise RuntimeError("db locked")

        audit_logger._sink = bad_sink
        audit_logger.emit("controlplane.test")

        payload = lifecycle.status_payload(runtime)
        assert payload["audit_health"]["audit"]["healthy"] is False
        assert payload["audit_health"]["audit"]["failures"] == 1
        assert (
            payload["audit_health"]["audit"]["last_error"] == "RuntimeError: db locked"
        )
        assert payload["audit_health"]["binding_warnings"] == 0
        assert payload["audit_health"]["wizard_step_failures"] == 0
    finally:
        _close_runtime(runtime)
