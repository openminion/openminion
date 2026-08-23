from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from openminion.modules.tool.contracts.computer_action_lifecycle import (
    ActionAuditProjectionV1,
    ActionRecordV1,
    create_or_replay,
    create_record,
    project_action_audit,
    transition,
)
from openminion.modules.tool.contracts.computer_actions import (
    ActionAcknowledgementV1,
    ActionCancellationV1,
    ActionControlFactV1,
    ActionDispatchFactV1,
    ActionErrorV1,
    ActionExpireFactV1,
    ActionInvocationV1,
    ActionResultV1,
    ActionTargetV1,
    AppliedApprovalFactV1,
    CapabilityStateV1,
    ComputerActionContractError,
    DesktopGrantV1,
    ERROR_MESSAGES,
    TrustedActionContextV1,
    TrustedCaptureFrameV1,
    bind_action_target,
    build_action_invocation,
    canonical_json,
    canonical_sha256,
    match_desktop_grant,
    parse_action_intent,
)

ACTION_ID = "a" * 48
GRANT_ID = "b" * 48
FRAME_ID = "c" * 48
POST_FRAME_ID = "d" * 48
CAPTURE_ID = "e" * 48
IDEMPOTENCY_KEY = "f" * 64
REQUESTED_AT = "2026-08-22T12:00:00.000Z"
DISPATCHED_AT = "2026-08-22T12:00:00.100Z"
ACKNOWLEDGED_AT = "2026-08-22T12:00:00.200Z"
CANCELLED_AT = "2026-08-22T12:00:00.300Z"
STARTED_AT = "2026-08-22T12:00:00.400Z"
ENDED_AT = "2026-08-22T12:00:00.500Z"
EXPIRES_AT = "2026-08-22T12:00:03.000Z"
FRAME_CAPTURED_AT = "2026-08-22T11:59:59.000Z"
FRAME_EXPIRES_AT = "2026-08-22T12:00:04.000Z"


def _error(code: str) -> ActionErrorV1:
    messages = {
        "cancelled": "The action was cancelled.",
        "executor_unavailable": "The desktop executor is unavailable.",
        "expired": "The action has expired.",
        "grant_revoked": "The desktop grant is not active.",
        "target_changed": "The action target has changed.",
    }
    return ActionErrorV1(code=code, message=messages[code])


def _target() -> ActionTargetV1:
    return ActionTargetV1(
        capture_id=CAPTURE_ID,
        frame_id=FRAME_ID,
        source_kind="display",
        parent_source_kind="display",
        frame_width=1440,
        frame_height=900,
        scale_millis=2000,
        frame_captured_at=FRAME_CAPTURED_AT,
        frame_expires_at=FRAME_EXPIRES_AT,
    )


def _intent(kind: str = "click", risk: str = "low") -> dict[str, object]:
    actions: dict[str, dict[str, object]] = {
        "pointer_move": {
            "kind": "pointer_move",
            "x_bps": 2500,
            "y_bps": 7500,
            "duration_ms": 120,
        },
        "click": {
            "kind": "click",
            "x_bps": 2500,
            "y_bps": 7500,
            "button": "left",
            "count": 1,
        },
        "scroll": {"kind": "scroll", "delta_x_bps": 0, "delta_y_bps": 500},
        "key_press": {
            "kind": "key_press",
            "keys": ["a", "enter"],
            "modifiers": ["control"],
        },
        "wait": {"kind": "wait", "duration_ms": 250},
    }
    return {"schema_version": 1, "action": actions[kind], "requested_risk": risk}


def _context(*, approval: bool = True) -> TrustedActionContextV1:
    approval_fact = (
        AppliedApprovalFactV1(
            approval_id="approval-safe",
            session_id="session-safe",
            trace_id="trace-safe",
            tool_call_id="tool-call-safe",
            actor_id="actor-safe",
            state="applied",
        )
        if approval
        else None
    )
    return TrustedActionContextV1(
        action_id=ACTION_ID,
        idempotency_key=IDEMPOTENCY_KEY,
        daemon_id="daemon-safe",
        desktop_client_id="client-safe",
        user_id="user-safe",
        session_id="session-safe",
        trace_id="trace-safe",
        turn_id="turn-safe",
        tool_call_id="tool-call-safe",
        actor_id="actor-safe",
        actor_kind="agent",
        target=_target(),
        requested_at=REQUESTED_AT,
        expires_at=EXPIRES_AT,
        approval=approval_fact,
        registered_risk_floor="high",
    )


def _invocation(*, kind: str = "click", risk: str = "low") -> ActionInvocationV1:
    return build_action_invocation(_intent(kind, risk), _context())


def _identity(invocation: ActionInvocationV1) -> dict[str, object]:
    return {
        key: getattr(invocation, key)
        for key in (
            "schema_version",
            "action_id",
            "idempotency_key",
            "daemon_id",
            "desktop_client_id",
            "user_id",
            "session_id",
            "trace_id",
            "turn_id",
            "tool_call_id",
            "actor_id",
        )
    }


def _dispatch(invocation: ActionInvocationV1) -> ActionDispatchFactV1:
    return ActionDispatchFactV1(
        **_identity(invocation), kind="dispatched", dispatched_at=DISPATCHED_AT
    )


def _ack(invocation: ActionInvocationV1) -> ActionAcknowledgementV1:
    return ActionAcknowledgementV1(
        **_identity(invocation),
        state="accepted",
        acknowledged_at=ACKNOWLEDGED_AT,
        error=None,
    )


def _cancel(invocation: ActionInvocationV1) -> ActionCancellationV1:
    return ActionCancellationV1(
        **_identity(invocation), reason="user_stop", requested_at=CANCELLED_AT
    )


def _result(
    invocation: ActionInvocationV1,
    *,
    state: str = "delivered",
    error: ActionErrorV1 | None = None,
) -> ActionResultV1:
    return ActionResultV1(
        **_identity(invocation),
        state=state,
        started_at=STARTED_AT,
        ended_at=ENDED_AT,
        duration_ms=100,
        post_action_frame_id=POST_FRAME_ID if state == "delivered" else None,
        error=error,
    )


def _grant(invocation: ActionInvocationV1) -> DesktopGrantV1:
    return DesktopGrantV1(
        schema_version=1,
        grant_id=GRANT_ID,
        daemon_id=invocation.daemon_id,
        desktop_client_id=invocation.desktop_client_id,
        user_id=invocation.user_id,
        session_id=invocation.session_id,
        actor_id=invocation.actor_id,
        actor_kind="agent",
        capabilities=[invocation.capability],
        action_kinds=[invocation.action.kind],
        target=invocation.target,
        issued_at=REQUESTED_AT,
        expires_at=EXPIRES_AT,
        state="active",
        revoked_at=None,
        revocation_reason=None,
    )


@pytest.mark.parametrize(
    "kind", ["pointer_move", "click", "scroll", "key_press", "wait"]
)
def test_all_bounded_action_kinds_parse(kind: str) -> None:
    parsed = parse_action_intent(_intent(kind))
    assert parsed.action.kind == kind


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (
            {
                "schema_version": 1,
                "action": {"kind": "text_entry", "text": "do-not-log"},
                "requested_risk": "high",
            },
            "unsupported_action",
        ),
        (
            {
                "schema_version": 1,
                "action": {"kind": "shell"},
                "requested_risk": "high",
            },
            "unsupported_action",
        ),
        ({**_intent(), "session_id": "spoof"}, "invalid_action"),
        ({**_intent(), "schema_version": 2}, "invalid_action"),
        ({**_intent(), "requested_risk": 1.5}, "invalid_action"),
    ],
)
def test_untrusted_intent_fails_with_fixed_safe_errors(
    payload: dict[str, object], code: str
) -> None:
    with pytest.raises(ComputerActionContractError) as caught:
        parse_action_intent(payload)
    assert caught.value.code == code
    assert "do-not-log" not in str(caught.value)
    assert caught.value.__context__ is None


def test_duplicate_json_key_is_rejected_before_validation() -> None:
    payload = '{"schema_version":1,"schema_version":1,"action":{"kind":"wait","duration_ms":1},"requested_risk":"low"}'
    with pytest.raises(ComputerActionContractError, match="invalid") as caught:
        parse_action_intent(payload)
    assert caught.value.code == "invalid_action"


def test_trusted_builder_preserves_critical_and_never_lowers_high_floor() -> None:
    low = _invocation(risk="low")
    critical = _invocation(risk="critical")
    assert low.effective_risk == "high"
    assert critical.effective_risk == "critical"
    assert low.approval_mode == "approve_each"
    assert low.capability == "computer.click"


def test_trusted_capture_binding_derives_scale_and_rejects_window_or_stale_frame() -> (
    None
):
    frame = TrustedCaptureFrameV1(
        session_id="session-safe",
        capture_id=CAPTURE_ID,
        frame_id=FRAME_ID,
        source_kind="display",
        parent_source_kind="display",
        frame_width=1440,
        frame_height=900,
        scale_factor=1.9996,
        captured_at=FRAME_CAPTURED_AT,
        expires_at=FRAME_EXPIRES_AT,
        observing=True,
    )
    target = bind_action_target(
        frame,
        session_id="session-safe",
        capture_id=CAPTURE_ID,
        frame_id=FRAME_ID,
        requested_at=REQUESTED_AT,
    )
    assert target.scale_millis == 2000

    window = TrustedCaptureFrameV1(
        **{**frame.__dict__, "source_kind": "window", "scale_factor": None}
    )
    with pytest.raises(ComputerActionContractError) as unsupported:
        bind_action_target(
            window,
            session_id="session-safe",
            capture_id=CAPTURE_ID,
            frame_id=FRAME_ID,
            requested_at=REQUESTED_AT,
        )
    assert unsupported.value.code == "unsupported_action"

    for scale in (0.4996, 4.0004):
        invalid_scale = TrustedCaptureFrameV1(
            **{**frame.__dict__, "scale_factor": scale}
        )
        with pytest.raises(ComputerActionContractError) as invalid:
            bind_action_target(
                invalid_scale,
                session_id="session-safe",
                capture_id=CAPTURE_ID,
                frame_id=FRAME_ID,
                requested_at=REQUESTED_AT,
            )
        assert invalid.value.code == "unsupported_action"

    simultaneous = TrustedCaptureFrameV1(
        **{**frame.__dict__, "captured_at": REQUESTED_AT}
    )
    with pytest.raises(ComputerActionContractError) as stale:
        bind_action_target(
            simultaneous,
            session_id="session-safe",
            capture_id=CAPTURE_ID,
            frame_id=FRAME_ID,
            requested_at=REQUESTED_AT,
        )
    assert stale.value.code == "frame_stale"

    malformed = TrustedCaptureFrameV1(
        **{**frame.__dict__, "captured_at": "secret-path-token"}
    )
    with pytest.raises(ComputerActionContractError) as safe:
        bind_action_target(
            malformed,
            session_id="session-safe",
            capture_id=CAPTURE_ID,
            frame_id=FRAME_ID,
            requested_at=REQUESTED_AT,
        )
    assert safe.value.code == "invalid_action"
    assert safe.value.__context__ is None


def test_trusted_builder_requires_matching_applied_approval() -> None:
    with pytest.raises(ComputerActionContractError) as missing:
        build_action_invocation(_intent(), _context(approval=False))
    assert missing.value.code == "approval_required"

    context = _context()
    mismatched = AppliedApprovalFactV1(
        **{**context.approval.__dict__, "trace_id": "wrong-trace"}
    )
    with pytest.raises(ComputerActionContractError) as mismatch:
        build_action_invocation(
            _intent(),
            TrustedActionContextV1(**{**context.__dict__, "approval": mismatched}),
        )
    assert mismatch.value.code == "approval_mismatch"

    for invalid_id in ("   ", "daemon\u0085unsafe"):
        invalid_context = TrustedActionContextV1(
            **{**context.__dict__, "daemon_id": invalid_id}
        )
        with pytest.raises(ComputerActionContractError) as invalid:
            build_action_invocation(_intent(), invalid_context)
        assert invalid.value.code == "invalid_action"
        assert invalid.value.__context__ is None

    simultaneous_target = _target().model_copy(
        update={"frame_captured_at": REQUESTED_AT}
    )
    invalid_target_context = TrustedActionContextV1(
        **{**context.__dict__, "target": simultaneous_target}
    )
    with pytest.raises(ComputerActionContractError) as invalid_target:
        build_action_invocation(_intent(), invalid_target_context)
    assert invalid_target.value.code == "invalid_action"
    assert invalid_target.value.__context__ is None


def test_grant_matches_all_authoritative_identity_target_and_capability_facts() -> None:
    invocation = _invocation()
    grant = _grant(invocation)
    assert match_desktop_grant(invocation, grant, ACKNOWLEDGED_AT) is grant

    wrong = grant.model_copy(update={"session_id": "other-session"})
    with pytest.raises(ComputerActionContractError) as caught:
        match_desktop_grant(invocation, wrong, ACKNOWLEDGED_AT)
    assert caught.value.code == "grant_required"

    revoked = grant.model_copy(
        update={
            "state": "revoked",
            "revoked_at": ACKNOWLEDGED_AT,
            "revocation_reason": "user_stop",
        }
    )
    wrong_revoked = revoked.model_copy(update={"session_id": "other-session"})
    with pytest.raises(ComputerActionContractError) as mismatch:
        match_desktop_grant(invocation, wrong_revoked, ACKNOWLEDGED_AT)
    assert mismatch.value.code == "grant_required"

    with pytest.raises(ValidationError):
        DesktopGrantV1.model_validate(
            {
                **revoked.model_dump(mode="json"),
                "revocation_reason": "grant_revoked",
            }
        )

    not_yet_issued = grant.model_copy(update={"issued_at": ACKNOWLEDGED_AT})
    with pytest.raises(ComputerActionContractError) as early:
        match_desktop_grant(invocation, not_yet_issued, DISPATCHED_AT)
    assert early.value.code == "grant_revoked"


def test_grant_and_capability_state_coupling_fail_closed() -> None:
    invocation = _invocation()
    with pytest.raises(ValidationError):
        DesktopGrantV1.model_validate(
            {**_grant(invocation).model_dump(), "capabilities": ["computer.wait"]}
        )
    with pytest.raises(ValidationError):
        CapabilityStateV1(
            schema_version=1,
            daemon_id="daemon-safe",
            desktop_client_id="client-safe",
            user_id="user-safe",
            session_id="session-safe",
            capability="computer.click",
            availability="available",
            reason="permission_missing",
            observed_at=REQUESTED_AT,
        )


def test_delivered_lifecycle_and_audit_projection_are_exact_and_redacted() -> None:
    invocation = _invocation()
    pending = create_record(invocation, REQUESTED_AT)
    dispatched = transition(pending, _dispatch(invocation), DISPATCHED_AT)
    accepted = transition(dispatched, _ack(invocation), ACKNOWLEDGED_AT)
    terminal = transition(accepted, _result(invocation), ENDED_AT)
    audit = project_action_audit(terminal)

    assert terminal.phase == "terminal"
    assert terminal.terminal_state == "delivered"
    assert audit.phase == "terminal"
    assert audit.action_id == ACTION_ID
    assert audit.approval_present is True
    public = canonical_json(audit)
    for forbidden in (
        IDEMPOTENCY_KEY,
        "daemon-safe",
        "client-safe",
        "user-safe",
        "actor-safe",
        "approval-safe",
        GRANT_ID,
        "x_bps",
    ):
        assert forbidden not in public


def test_undispatched_cancellation_is_safe_rejection() -> None:
    invocation = _invocation()
    terminal = transition(
        create_record(invocation, REQUESTED_AT), _cancel(invocation), CANCELLED_AT
    )
    assert terminal.terminal_state == "rejected"
    assert terminal.error == _error("cancelled")
    assert terminal.dispatch is None


def test_dispatched_cancellation_accepts_late_ack_and_matching_result() -> None:
    invocation = _invocation()
    record = transition(
        create_record(invocation, REQUESTED_AT), _dispatch(invocation), DISPATCHED_AT
    )
    record = transition(record, _cancel(invocation), CANCELLED_AT)
    record = transition(record, _ack(invocation), CANCELLED_AT)
    assert record.phase == "cancel_requested"
    assert record.pre_cancel_phase == "accepted"
    interrupted = _result(
        invocation, state="interrupted_before_delivery", error=_error("cancelled")
    )
    terminal = transition(record, interrupted, ENDED_AT)
    assert terminal.terminal_state == "interrupted_before_delivery"


def test_late_rejected_ack_retains_dispatch_and_cancellation_history() -> None:
    invocation = _invocation()
    record = transition(
        create_record(invocation, REQUESTED_AT), _dispatch(invocation), DISPATCHED_AT
    )
    record = transition(record, _cancel(invocation), CANCELLED_AT)
    rejected = ActionAcknowledgementV1(
        **_identity(invocation),
        state="rejected",
        acknowledged_at=ACKNOWLEDGED_AT,
        error=_error("grant_revoked"),
    )
    terminal = transition(record, rejected, CANCELLED_AT)
    assert terminal.dispatch == _dispatch(invocation)
    assert terminal.cancellation == _cancel(invocation)
    assert terminal.terminal_state == "rejected"
    assert terminal.error == _error("grant_revoked")


def test_expiry_is_persisted_idempotent_and_preserves_first_cancel_reason() -> None:
    invocation = _invocation()
    dispatched = transition(
        create_record(invocation, REQUESTED_AT), _dispatch(invocation), DISPATCHED_AT
    )
    cancelling = transition(dispatched, _cancel(invocation), CANCELLED_AT)
    expiry = ActionExpireFactV1(
        **_identity(invocation), kind="expire", observed_at=EXPIRES_AT
    )
    expired = transition(cancelling, expiry, EXPIRES_AT)
    assert expired.expiry == expiry
    assert expired.cancellation.reason == "user_stop"
    assert transition(expired, expiry, EXPIRES_AT) is expired

    terminal = transition(create_record(invocation, REQUESTED_AT), expiry, EXPIRES_AT)
    assert terminal.terminal_state == "rejected"
    assert terminal.error == _error("expired")
    later_expiry = expiry.model_copy(update={"observed_at": FRAME_EXPIRES_AT})
    assert transition(terminal, later_expiry, FRAME_EXPIRES_AT) is terminal


@pytest.mark.parametrize("accepted", [False, True])
def test_expiry_after_dispatch_requests_cancellation(accepted: bool) -> None:
    invocation = _invocation()
    record = transition(
        create_record(invocation, REQUESTED_AT),
        _dispatch(invocation),
        DISPATCHED_AT,
    )
    if accepted:
        record = transition(record, _ack(invocation), ACKNOWLEDGED_AT)
    expiry = ActionExpireFactV1(
        **_identity(invocation), kind="expire", observed_at=EXPIRES_AT
    )
    cancelling = transition(record, expiry, EXPIRES_AT)
    assert cancelling.phase == "cancel_requested"
    assert cancelling.expiry == expiry
    assert cancelling.cancellation.reason == "expired"
    assert cancelling.cancellation.requested_at == EXPIRES_AT


def test_expiry_requires_clock_fact_at_or_after_invocation_expiry() -> None:
    invocation = _invocation()
    record = create_record(invocation, REQUESTED_AT)
    early = ActionExpireFactV1(
        **_identity(invocation), kind="expire", observed_at=ACKNOWLEDGED_AT
    )
    with pytest.raises(ComputerActionContractError) as early_error:
        transition(record, early, ACKNOWLEDGED_AT)
    assert early_error.value.code == "result_conflict"

    direct = ActionCancellationV1(
        **_identity(invocation), reason="expired", requested_at=EXPIRES_AT
    )
    with pytest.raises(ComputerActionContractError) as direct_error:
        transition(record, direct, EXPIRES_AT)
    assert direct_error.value.code == "result_conflict"

    terminal = transition(
        record,
        expiry := ActionExpireFactV1(
            **_identity(invocation), kind="expire", observed_at=EXPIRES_AT
        ),
        EXPIRES_AT,
    )
    with pytest.raises(ComputerActionContractError) as wrong_now:
        transition(terminal, expiry, FRAME_EXPIRES_AT)
    assert wrong_now.value.code == "result_conflict"

    delivered = transition(
        transition(
            transition(record, _dispatch(invocation), DISPATCHED_AT),
            _ack(invocation),
            ACKNOWLEDGED_AT,
        ),
        _result(invocation),
        ENDED_AT,
    )
    with pytest.raises(ComputerActionContractError) as terminal_early:
        transition(delivered, early, EXPIRES_AT)
    assert terminal_early.value.code == "result_conflict"


@pytest.mark.parametrize("kind", ["disconnect", "restart", "executor_unavailable"])
def test_control_facts_never_resume_or_claim_non_delivery(kind: str) -> None:
    invocation = _invocation()
    dispatched = transition(
        create_record(invocation, REQUESTED_AT), _dispatch(invocation), DISPATCHED_AT
    )
    control = ActionControlFactV1(
        **_identity(invocation), kind=kind, observed_at=CANCELLED_AT
    )
    terminal = transition(dispatched, control, CANCELLED_AT)
    assert terminal.terminal_state == "outcome_unknown"
    assert terminal.error == _error("executor_unavailable")


def test_pending_control_rejects_before_dispatch() -> None:
    invocation = _invocation()
    pending = create_record(invocation, REQUESTED_AT)
    control = ActionControlFactV1(
        **_identity(invocation), kind="disconnect", observed_at=DISPATCHED_AT
    )
    terminal = transition(pending, control, DISPATCHED_AT)
    assert terminal.terminal_state == "rejected"
    assert terminal.dispatch is None
    assert terminal.error == _error("executor_unavailable")


def test_accepted_cancellation_and_unknown_result_are_explicit() -> None:
    invocation = _invocation()
    accepted = transition(
        transition(
            create_record(invocation, REQUESTED_AT),
            _dispatch(invocation),
            DISPATCHED_AT,
        ),
        _ack(invocation),
        ACKNOWLEDGED_AT,
    )
    cancelling = transition(accepted, _cancel(invocation), CANCELLED_AT)
    assert cancelling.phase == "cancel_requested"
    assert cancelling.pre_cancel_phase == "accepted"

    unknown_result = _result(
        invocation,
        state="outcome_unknown",
        error=_error("executor_unavailable"),
    )
    terminal = transition(accepted, unknown_result, ENDED_AT)
    assert terminal.terminal_state == "outcome_unknown"
    assert terminal.result == unknown_result


def test_replay_and_conflicting_terminal_result_fail_closed() -> None:
    invocation = _invocation()
    record = create_record(invocation, REQUESTED_AT)
    assert create_or_replay(record, invocation, ACKNOWLEDGED_AT) is record
    changed = invocation.model_copy(
        update={"requested_risk": "critical", "effective_risk": "critical"}
    )
    with pytest.raises(ComputerActionContractError) as replay:
        create_or_replay(record, changed, ACKNOWLEDGED_AT)
    assert replay.value.code == "replay_conflict"

    delivered = transition(
        transition(
            transition(record, _dispatch(invocation), DISPATCHED_AT),
            _ack(invocation),
            ACKNOWLEDGED_AT,
        ),
        _result(invocation),
        ENDED_AT,
    )
    with pytest.raises(ComputerActionContractError) as conflict:
        transition(delivered, _cancel(invocation), ENDED_AT)
    assert conflict.value.code == "result_conflict"

    with pytest.raises(ComputerActionContractError) as noncanonical_now:
        create_or_replay(record, invocation, "not-a-time")
    assert noncanonical_now.value.code == "result_conflict"
    assert noncanonical_now.value.__context__ is None
    with pytest.raises(ComputerActionContractError) as earlier_now:
        create_or_replay(record, invocation, FRAME_CAPTURED_AT)
    assert earlier_now.value.code == "result_conflict"


def test_dispatched_origin_result_without_ack_is_truthful() -> None:
    invocation = _invocation()
    dispatched = transition(
        create_record(invocation, REQUESTED_AT),
        _dispatch(invocation),
        DISPATCHED_AT,
    )
    cancelling = transition(dispatched, _cancel(invocation), CANCELLED_AT)
    terminal = transition(cancelling, _result(invocation), ENDED_AT)
    assert terminal.acknowledgement is None
    assert terminal.terminal_state == "delivered"


def test_record_deserialization_rejects_causal_time_drift() -> None:
    invocation = _invocation()
    dispatched = transition(
        create_record(invocation, REQUESTED_AT),
        _dispatch(invocation),
        DISPATCHED_AT,
    )
    invalid_dispatch = dispatched.model_dump(mode="json")
    invalid_dispatch["dispatch"]["dispatched_at"] = FRAME_CAPTURED_AT
    with pytest.raises(ValidationError):
        ActionRecordV1.model_validate(invalid_dispatch)

    accepted = transition(dispatched, _ack(invocation), ACKNOWLEDGED_AT)
    delivered = transition(accepted, _result(invocation), ENDED_AT)
    invalid_update = delivered.model_dump(mode="json")
    invalid_update["updated_at"] = STARTED_AT
    with pytest.raises(ValidationError):
        ActionRecordV1.model_validate(invalid_update)


def test_terminal_records_have_one_terminal_fact_owner() -> None:
    invocation = _invocation()
    pending = create_record(invocation, REQUESTED_AT)
    accepted = transition(
        transition(
            create_record(invocation, REQUESTED_AT),
            _dispatch(invocation),
            DISPATCHED_AT,
        ),
        _ack(invocation),
        ACKNOWLEDGED_AT,
    )
    delivered = transition(accepted, _result(invocation), ENDED_AT)
    control = ActionControlFactV1(
        **_identity(invocation), kind="disconnect", observed_at=ENDED_AT
    )
    conflicting_delivered = delivered.model_dump(mode="json")
    conflicting_delivered["control"] = control.model_dump(mode="json")
    with pytest.raises(ValidationError):
        ActionRecordV1.model_validate(conflicting_delivered)

    unknown = transition(accepted, control, ENDED_AT)
    conflicting_unknown = unknown.model_dump(mode="json")
    conflicting_unknown["result"] = _result(
        invocation,
        state="outcome_unknown",
        error=_error("executor_unavailable"),
    ).model_dump(mode="json")
    with pytest.raises(ValidationError):
        ActionRecordV1.model_validate(conflicting_unknown)

    rejected = transition(pending, _cancel(invocation), CANCELLED_AT)
    conflicting_rejected = rejected.model_dump(mode="json")
    conflicting_rejected["control"] = control.model_dump(mode="json")
    with pytest.raises(ValidationError):
        ActionRecordV1.model_validate(conflicting_rejected)

    rejected_ack = ActionAcknowledgementV1(
        **_identity(invocation),
        state="rejected",
        acknowledged_at=ACKNOWLEDGED_AT,
        error=_error("grant_revoked"),
    )
    ack_terminal = transition(
        transition(pending, _dispatch(invocation), DISPATCHED_AT),
        rejected_ack,
        ACKNOWLEDGED_AT,
    )
    mismatched_error = ack_terminal.model_dump(mode="json")
    mismatched_error["error"] = _error("target_changed").model_dump(mode="json")
    with pytest.raises(ValidationError):
        ActionRecordV1.model_validate(mismatched_error)


def test_terminal_result_requires_reachable_prehistory() -> None:
    invocation = _invocation()
    pending = create_record(invocation, REQUESTED_AT)
    dispatched = transition(pending, _dispatch(invocation), DISPATCHED_AT)
    result = _result(invocation)

    no_ack_or_cancel = dispatched.model_dump(mode="json")
    no_ack_or_cancel.update(
        phase="terminal",
        result=result.model_dump(mode="json"),
        terminal_state="delivered",
        updated_at=ENDED_AT,
    )
    with pytest.raises(ValidationError):
        ActionRecordV1.model_validate(no_ack_or_cancel)

    rejected_ack = ActionAcknowledgementV1(
        **_identity(invocation),
        state="rejected",
        acknowledged_at=ACKNOWLEDGED_AT,
        error=_error("grant_revoked"),
    )
    rejected = transition(dispatched, rejected_ack, ACKNOWLEDGED_AT)
    rejected_then_delivered = rejected.model_dump(mode="json")
    rejected_then_delivered.update(
        result=result.model_dump(mode="json"),
        terminal_state="delivered",
        error=None,
        updated_at=ENDED_AT,
    )
    with pytest.raises(ValidationError):
        ActionRecordV1.model_validate(rejected_then_delivered)

    cancelling = transition(dispatched, _cancel(invocation), CANCELLED_AT)
    mismatched_result = _result(
        invocation,
        state="interrupted_before_delivery",
        error=_error("grant_revoked"),
    )
    mismatched_cancel = cancelling.model_dump(mode="json")
    mismatched_cancel.update(
        phase="terminal",
        pre_cancel_phase=None,
        result=mismatched_result.model_dump(mode="json"),
        terminal_state="interrupted_before_delivery",
        error=mismatched_result.error.model_dump(mode="json"),
        updated_at=ENDED_AT,
    )
    with pytest.raises(ValidationError):
        ActionRecordV1.model_validate(mismatched_cancel)


def test_contract_is_not_registered_as_tool() -> None:
    from openminion.modules.tool import build_default_tool_registry

    registry = build_default_tool_registry()
    contract_modules = {
        "openminion.modules.tool.contracts.computer_actions",
        "openminion.modules.tool.contracts.computer_action_lifecycle",
    }
    assert all(
        type(tool).__module__ not in contract_modules
        for tool in registry.list().values()
    )
    intent_keys = {"schema_version", "action", "requested_risk"}
    exposed = registry.provider_specs() + registry.model_provider_specs()
    assert all(
        set(spec.parameters.get("properties", {})) != intent_keys for spec in exposed
    )


def test_import_is_dependency_neutral() -> None:
    probe = """
import json
import sys
before = set(sys.modules)
import openminion.modules.tool.contracts.computer_actions
import openminion.modules.tool.contracts.computer_action_lifecycle
added = set(sys.modules) - before
forbidden = (
    'openminion.api', 'openminion.cli', 'openminion.providers',
    'openminion.services', 'openminion.modules.policy',
    'openminion.modules.session', 'openminion.modules.tool.registry',
    'electron', 'playwright', 'pyautogui',
)
print(json.dumps(sorted(name for name in added if name.startswith(forbidden))))
"""
    result = subprocess.run(
        [sys.executable, "-c", probe],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(result.stdout) == []


def test_golden_contract_vectors() -> None:
    fixture_env = __import__("os").environ.get("OPENMINION_COMPUTER_ACTION_V1_FIXTURE")
    if fixture_env:
        fixture_path = Path(fixture_env)
    else:
        fixture_path = (
            Path(__file__).resolve().parents[3]
            / "docs"
            / "trackers"
            / "artifacts"
            / "openminion-desktop-computer-actions-v1.json"
        )
        if not fixture_path.exists():
            pytest.skip("package checkout does not include the docs fixture")
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    parsed = parse_action_intent(fixture["valid_intent"])
    assert canonical_json(parsed) == fixture["valid_intent_canonical"]
    assert canonical_sha256(parsed) == fixture["valid_intent_sha256"]
    reverse_models = {
        "invocation": ActionInvocationV1,
        "capability_state": CapabilityStateV1,
        "grant": DesktopGrantV1,
        "dispatch": ActionDispatchFactV1,
        "expiry": ActionExpireFactV1,
        "acknowledgement": ActionAcknowledgementV1,
        "cancellation": ActionCancellationV1,
        "control": ActionControlFactV1,
        "result": ActionResultV1,
        "delivered_record": ActionRecordV1,
        "audit": ActionAuditProjectionV1,
    }
    for key, model in reverse_models.items():
        normalized = model.model_validate(fixture[key])
        assert canonical_json(normalized) == canonical_json(fixture[key])
    assert fixture["fixed_errors"] == ERROR_MESSAGES

    invocation = ActionInvocationV1.model_validate(fixture["invocation"])
    derived = create_record(invocation, invocation.requested_at)
    for key, now in (
        ("dispatch", fixture["dispatch"]["dispatched_at"]),
        ("acknowledgement", fixture["acknowledgement"]["acknowledged_at"]),
        ("result", fixture["result"]["ended_at"]),
    ):
        derived = transition(
            derived, reverse_models[key].model_validate(fixture[key]), now
        )
    assert canonical_json(derived) == canonical_json(fixture["delivered_record"])
    assert canonical_json(project_action_audit(derived)) == canonical_json(
        fixture["audit"]
    )

    for negative in fixture["negative_cases"]:
        if negative["name"] in {"model_text", "spoofed_session"}:
            with pytest.raises(ComputerActionContractError) as error:
                parse_action_intent(negative["input"])
            assert error.value.code == negative["error"]
        elif negative["name"] == "lowered_effective_risk":
            invalid = dict(fixture["invocation"])
            invalid[negative["field"]] = negative["value"]
            with pytest.raises(ValidationError):
                ActionInvocationV1.model_validate(invalid)
            assert negative["error"] == "invalid_action"
        else:
            changed = dict(fixture["invocation"])
            changed[negative["field"]] = negative["value"]
            changed["effective_risk"] = "critical"
            with pytest.raises(ComputerActionContractError) as error:
                create_or_replay(
                    ActionRecordV1.model_validate(fixture["delivered_record"]),
                    ActionInvocationV1.model_validate(changed),
                    fixture["delivered_record"]["updated_at"],
                )
            assert error.value.code == negative["error"]
