from __future__ import annotations

from types import SimpleNamespace

from openminion.modules.brain.diagnostics import telemetry
from openminion.modules.brain.execution.runtime.turn import dispatch as turn_dispatch
from openminion.modules.brain.schemas import ActDecision, BudgetCounters, WorkingState
from openminion.modules.brain.schemas import RequestReadiness


def test_request_readiness_telemetry_emits_bounded_fields(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def _emit(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(telemetry, "emit_brain_operation", _emit)

    telemetry.emit_request_readiness_operation(
        telemetryctl="telemetryctl",
        session_id="sess",
        turn_id="trace",
        readiness=RequestReadiness(
            posture="brief_plan",
            requested_outcome="execute",
            state="ready",
            assumptions=[
                {
                    "text": "Use existing fixture.",
                    "source": "repository",
                    "reversible": True,
                    "validation_trigger": "test fails",
                }
            ],
        ),
    )

    assert calls == [
        {
            "telemetryctl": "telemetryctl",
            "session_id": "sess",
            "turn_id": "trace",
            "operation": "request_readiness",
            "status": "ok",
            "extra": {
                "present": True,
                "posture": "brief_plan",
                "requested_outcome": "execute",
                "state": "ready",
                "assumption_count": 1,
            },
        }
    ]


def test_request_readiness_telemetry_failure_does_not_block_turn(
    monkeypatch,
) -> None:
    class StubEntryBarrel:
        def _sync_typed_decision_signals(self, **_kwargs):
            return None

        def write_decision_memory(self, *_args, **_kwargs):
            return []

    class StubLogger:
        def __init__(self) -> None:
            self.events: list[tuple[str, dict[str, object], str | None]] = []

        def emit(self, event: str, payload: dict[str, object], **kwargs) -> None:
            self.events.append((event, payload, kwargs.get("status")))

    def _raise_telemetry(**_kwargs):
        raise RuntimeError("sink unavailable")

    monkeypatch.setattr(turn_dispatch, "_entry_barrel", lambda: StubEntryBarrel())
    monkeypatch.setattr(
        turn_dispatch,
        "emit_request_readiness_operation",
        _raise_telemetry,
    )

    state = WorkingState(
        session_id="sess",
        agent_id="agent",
        budgets_remaining=BudgetCounters(
            ticks=4,
            tool_calls=2,
            a2a_calls=0,
            tokens=2000,
            time_ms=10000,
        ),
    )
    logger = StubLogger()

    turn_dispatch._record_accepted_decision(
        runner=SimpleNamespace(),
        state=state,
        logger=logger,
        request=SimpleNamespace(decision=ActDecision(), capability_category=None),
        user_input="continue",
    )

    assert logger.events == [
        (
            "brain.request_readiness.telemetry_failed",
            {"error_type": "RuntimeError"},
            "warning",
        )
    ]
