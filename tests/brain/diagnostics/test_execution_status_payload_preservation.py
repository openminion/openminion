from __future__ import annotations

from types import SimpleNamespace

from openminion.modules.brain.execution.lifecycle import emit_mode_status
from openminion.modules.brain.execution.services import RunnerExecutionServices


class _FakeLogger:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def emit(self, event_type: str, payload: dict[str, object], **kwargs) -> None:
        self.events.append(
            {
                "event_type": event_type,
                "payload": dict(payload),
                "meta": dict(kwargs),
            }
        )


class _FakeRunner:
    def __init__(self) -> None:
        self._trace_id = "runner-trace"
        self.phase_status_calls: list[dict[str, object]] = []

    def _emit_phase_status(self, **kwargs) -> None:
        self.phase_status_calls.append(dict(kwargs))


def test_emit_mode_status_preserves_raw_payload_fields_in_execution_status() -> None:
    logger = _FakeLogger()
    state = SimpleNamespace(trace_id="trace-coding-status")

    emit_mode_status(
        object(),
        state=state,
        logger=logger,
        source_phase="coding.plan",
        mode="act_loop_adaptive",
        mode_state="implement",
        payload={
            "coding.plan_phases_executed": ["implement", "verify"],
            "coding.current_phase": "implement",
            "resume_count": 1,
            "last_checkpoint_id": "coding-ckpt-1",
        },
    )

    assert len(logger.events) == 1
    event = logger.events[0]
    assert event["event_type"] == "brain.execution_status"
    payload = event["payload"]
    assert payload["coding.plan_phases_executed"] == ["implement", "verify"]
    assert payload["coding.current_phase"] == "implement"
    assert payload["resume_count"] == 1
    assert payload["last_checkpoint_id"] == "coding-ckpt-1"
    assert payload["route"] == "act_loop_adaptive"
    assert payload["status_key"] == "working"


def test_runner_execution_services_emits_coding_payloads_to_event_log() -> None:
    logger = _FakeLogger()
    runner = _FakeRunner()
    services = RunnerExecutionServices(runner=runner)
    state = SimpleNamespace(trace_id="trace-coding-phase")

    services.emit_phase_status(
        state=state,
        logger=logger,
        source_phase="coding.plan",
        mode="act_loop_adaptive",
        mode_state="verify",
        payload={
            "coding.plan_phases_executed": ["implement", "verify"],
            "coding.current_phase": "verify",
            "resume_count": 1,
            "last_checkpoint_id": "coding-ckpt-2",
        },
    )

    assert len(runner.phase_status_calls) == 1
    assert len(logger.events) == 1
    event = logger.events[0]
    assert event["event_type"] == "brain.execution_status"
    payload = event["payload"]
    assert payload["coding.plan_phases_executed"] == ["implement", "verify"]
    assert payload["coding.current_phase"] == "verify"
    assert payload["resume_count"] == 1
    assert payload["last_checkpoint_id"] == "coding-ckpt-2"
