"""Compatibility bridge for trust-gate audit events."""

from typing import Any

from sophiagraph.audit.events import (
    TrustGateDecision,
    TrustGateEvent,
    TrustGateReasonCode,
)


def emit_trust_gate_event(owner: Any, event: TrustGateEvent) -> None:
    if callable(append := getattr(getattr(owner, "_store", owner), "_append", None)):
        append(event.to_memory_audit_event())


__all__ = [
    "TrustGateDecision",
    "TrustGateEvent",
    "TrustGateReasonCode",
    "emit_trust_gate_event",
]
