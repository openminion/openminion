from __future__ import annotations

from dataclasses import dataclass, field

from openminion.modules.brain.runtime.safety import SafetyService
from openminion.modules.policy.runtime.security import (
    RISK_HIGH,
    RISK_LOW,
    SecurityPolicyAction,
    SecurityPolicyCheck,
    SecurityPolicyContext,
    SecurityPolicyEngine,
    default_internal_actor,
)


@dataclass
class _Telemetry:
    events: list[tuple[str, dict]] = field(default_factory=list)

    async def emit_canonical_event(
        self,
        session_id: str,
        turn_id: str,
        event_type: str,
        payload: dict,
        **kwargs,
    ) -> None:
        self.events.append((event_type, dict(payload)))


def _check(*, risk: str) -> SecurityPolicyCheck:
    return SecurityPolicyCheck(
        actor=default_internal_actor("agent-1"),
        action=SecurityPolicyAction(
            resource="tool",
            verb="execute",
            risk=risk,
            tool_name="shell",
        ),
        context=SecurityPolicyContext(
            session_id="session-1",
            run_id="run-1",
            turn_id="turn-1",
            trace_id="trace-1",
            invocation_id="invocation-1",
            execution_id="execution-1",
        ),
    )


def test_policy_owner_emits_allow_and_approval_without_payload_content() -> None:
    telemetry = _Telemetry()
    engine = SecurityPolicyEngine(telemetryctl=telemetry)
    assert engine.evaluate(_check(risk=RISK_LOW)).decision == "allow"
    assert engine.evaluate(_check(risk=RISK_HIGH)).decision == "require_approval"

    assert [event_type for event_type, _ in telemetry.events] == [
        "policy.decision",
        "policy.decision",
    ]
    approval = telemetry.events[-1][1]
    assert approval["approval_state"] == "required"
    assert approval["reason_code"] == "approval_required_high_risk"
    assert approval["invocation_id"] == "invocation-1"
    assert "details" not in approval


def test_policy_owner_emits_typed_denial() -> None:
    telemetry = _Telemetry()
    engine = SecurityPolicyEngine(telemetryctl=telemetry)
    check = _check(risk=RISK_LOW)
    object.__setattr__(
        check,
        "action",
        SecurityPolicyAction(resource="unknown", verb="noop", risk=RISK_LOW),
    )
    assert engine.evaluate(check).decision == "deny"
    assert telemetry.events[0][1]["reason_code"] == "unknown_action"


def test_safety_owner_emits_bounded_preemption_without_raw_reason() -> None:
    telemetry = _Telemetry()
    service = SafetyService(telemetryctl=telemetry)
    assert service.panic(
        session_id="session-1",
        reason="Credential leak in raw payload",
        metadata={
            "turn_id": "turn-1",
            "trace_id": "trace-1",
            "invocation_id": "invocation-1",
            "execution_id": "execution-1",
            "violation_category": "credential_boundary",
        },
    )

    event_type, payload = telemetry.events[0]
    assert event_type == "safety.preempted"
    assert payload["action"] == "panic"
    assert payload["violation_category"] == "credential_boundary"
    assert payload["reason_code"] == "credential_leak_in_raw_payload"
    assert "reason" not in payload
