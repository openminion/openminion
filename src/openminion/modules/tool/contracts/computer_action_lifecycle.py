"""Pure immutable lifecycle for validated desktop computer-action values."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from .computer_actions import (
    SCHEMA_VERSION,
    ActionAcknowledgementV1,
    ActionCancellationV1,
    ActionControlFactV1,
    ActionDispatchFactV1,
    ActionErrorV1,
    ActionExpireFactV1,
    ActionInvocationV1,
    ActionResultV1,
    Capability,
    ErrorCode,
    FactV1,
    Hex48,
    Hex64,
    Identity128,
    Identity256,
    Risk,
    Timestamp,
    _cancellation_error,
    _error,
    _fail,
    _identity_dict,
    _identity_tuple,
    _instant,
    _timestamp_or_fail,
    _validation_error,
    canonical_sha256,
)


class ActionRecordV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1]
    invocation: ActionInvocationV1
    invocation_hash: Hex64
    phase: Literal["pending", "dispatched", "accepted", "cancel_requested", "terminal"]
    pre_cancel_phase: Literal["dispatched", "accepted"] | None
    dispatch: ActionDispatchFactV1 | None
    acknowledgement: ActionAcknowledgementV1 | None
    cancellation: ActionCancellationV1 | None
    expiry: ActionExpireFactV1 | None
    control: ActionControlFactV1 | None
    result: ActionResultV1 | None
    terminal_state: (
        Literal[
            "rejected",
            "interrupted_before_delivery",
            "delivered",
            "outcome_unknown",
        ]
        | None
    )
    error: ActionErrorV1 | None
    created_at: Timestamp
    updated_at: Timestamp

    @model_validator(mode="after")
    def _coupled(self) -> ActionRecordV1:
        _validate_record_coupling(self)
        return self


class ActionAuditProjectionV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1]
    action_id: Hex48
    session_id: Identity256
    trace_id: Identity128
    turn_id: Identity128
    tool_call_id: Identity128
    actor_kind: Literal["agent"]
    capability: Capability
    action_kind: Literal["pointer_move", "click", "scroll", "key_press", "wait"]
    source_kind: Literal["display", "region"]
    requested_risk: Risk
    effective_risk: Literal["high", "critical"]
    approval_mode: Literal["approve_each"]
    approval_present: Literal[True]
    phase: Literal["pending", "dispatched", "accepted", "cancel_requested", "terminal"]
    terminal_state: (
        Literal[
            "rejected",
            "interrupted_before_delivery",
            "delivered",
            "outcome_unknown",
        ]
        | None
    )
    error_code: ErrorCode | None
    requested_at: Timestamp
    acknowledged_at: Timestamp | None
    started_at: Timestamp | None
    ended_at: Timestamp | None


def create_record(invocation: ActionInvocationV1, now: str) -> ActionRecordV1:
    _timestamp_or_fail(now)
    if _instant(invocation.requested_at) > _instant(now):
        _fail("result_conflict")
    if _instant(now) >= _instant(invocation.expires_at):
        _fail("expired")
    return ActionRecordV1(
        schema_version=SCHEMA_VERSION,
        invocation=invocation,
        invocation_hash=canonical_sha256(invocation),
        phase="pending",
        pre_cancel_phase=None,
        dispatch=None,
        acknowledgement=None,
        cancellation=None,
        expiry=None,
        control=None,
        result=None,
        terminal_state=None,
        error=None,
        created_at=now,
        updated_at=now,
    )


def create_or_replay(
    existing: ActionRecordV1 | None,
    invocation: ActionInvocationV1,
    now: str,
) -> ActionRecordV1:
    _timestamp_or_fail(now)
    if existing is None:
        return create_record(invocation, now)
    if _instant(now) < _instant(existing.updated_at):
        _fail("result_conflict")
    same_identity = (
        existing.invocation.action_id == invocation.action_id
        and existing.invocation.idempotency_key == invocation.idempotency_key
    )
    if same_identity and existing.invocation_hash == canonical_sha256(invocation):
        return existing
    _fail("replay_conflict")


def transition(record: ActionRecordV1, fact: FactV1, now: str) -> ActionRecordV1:
    """Apply one identity-bound lifecycle fact to one immutable record."""

    _validate_transition_admission(record, fact, now)
    if isinstance(fact, ActionExpireFactV1) and (
        fact.observed_at != now
        or _instant(now) < _instant(record.invocation.expires_at)
    ):
        _fail("result_conflict")
    if _stored_fact(record, fact) == fact:
        return record
    if record.phase == "terminal":
        if isinstance(fact, ActionExpireFactV1):
            return record
        _fail("result_conflict")
    if isinstance(fact, ActionDispatchFactV1):
        return _apply_dispatch(record, fact, now)
    if isinstance(fact, ActionExpireFactV1):
        return _apply_expiry(record, fact, now)
    if isinstance(fact, ActionAcknowledgementV1):
        return _apply_acknowledgement(record, fact, now)
    if isinstance(fact, ActionCancellationV1):
        return _apply_cancellation(record, fact, now)
    if isinstance(fact, ActionControlFactV1):
        return _apply_control(record, fact, now)
    return _apply_result(record, fact, now)


def project_action_audit(record: ActionRecordV1) -> ActionAuditProjectionV1:
    invocation = record.invocation
    result = record.result
    return ActionAuditProjectionV1(
        schema_version=SCHEMA_VERSION,
        action_id=invocation.action_id,
        session_id=invocation.session_id,
        trace_id=invocation.trace_id,
        turn_id=invocation.turn_id,
        tool_call_id=invocation.tool_call_id,
        actor_kind=invocation.actor_kind,
        capability=invocation.capability,
        action_kind=invocation.action.kind,
        source_kind=invocation.target.source_kind,
        requested_risk=invocation.requested_risk,
        effective_risk=invocation.effective_risk,
        approval_mode=invocation.approval_mode,
        approval_present=True,
        phase=record.phase,
        terminal_state=record.terminal_state,
        error_code=record.error.code if record.error else None,
        requested_at=invocation.requested_at,
        acknowledged_at=(
            record.acknowledgement.acknowledged_at if record.acknowledgement else None
        ),
        started_at=result.started_at if result else None,
        ended_at=result.ended_at if result else None,
    )


def _validate_transition_admission(
    record: ActionRecordV1, fact: FactV1, now: str
) -> None:
    _timestamp_or_fail(now)
    if _instant(now) < _instant(record.updated_at):
        _fail("result_conflict")
    if _identity_tuple(fact) != _identity_tuple(record.invocation):
        _fail("result_conflict")
    if _instant(_fact_timestamp(fact)) > _instant(now):
        _fail("result_conflict")


def _fact_timestamp(fact: FactV1) -> str:
    if isinstance(fact, ActionDispatchFactV1):
        return fact.dispatched_at
    if isinstance(fact, ActionAcknowledgementV1):
        return fact.acknowledged_at
    if isinstance(fact, ActionCancellationV1):
        return fact.requested_at
    if isinstance(fact, ActionResultV1):
        return fact.ended_at
    return fact.observed_at


def _stored_fact(record: ActionRecordV1, fact: FactV1) -> FactV1 | None:
    if isinstance(fact, ActionDispatchFactV1):
        return record.dispatch
    if isinstance(fact, ActionAcknowledgementV1):
        return record.acknowledgement
    if isinstance(fact, ActionCancellationV1):
        return record.cancellation
    if isinstance(fact, ActionExpireFactV1):
        return record.expiry
    if isinstance(fact, ActionControlFactV1):
        return record.control
    return record.result


def _apply_dispatch(
    record: ActionRecordV1, fact: ActionDispatchFactV1, now: str
) -> ActionRecordV1:
    invocation = record.invocation
    if record.phase != "pending" or not (
        _instant(invocation.requested_at)
        <= _instant(fact.dispatched_at)
        < _instant(invocation.expires_at)
    ):
        _fail("result_conflict")
    return record.model_copy(
        update={"phase": "dispatched", "dispatch": fact, "updated_at": now}
    )


def _apply_acknowledgement(
    record: ActionRecordV1, fact: ActionAcknowledgementV1, now: str
) -> ActionRecordV1:
    dispatch = record.dispatch
    if dispatch is None or _instant(fact.acknowledged_at) < _instant(
        dispatch.dispatched_at
    ):
        _fail("result_conflict")
    if fact.state == "accepted":
        return _apply_accepted_acknowledgement(record, fact, now)
    return _apply_rejected_acknowledgement(record, fact, now)


def _apply_accepted_acknowledgement(
    record: ActionRecordV1, fact: ActionAcknowledgementV1, now: str
) -> ActionRecordV1:
    if _instant(fact.acknowledged_at) >= _instant(record.invocation.expires_at):
        _fail("result_conflict")
    if record.phase == "dispatched":
        return record.model_copy(
            update={
                "phase": "accepted",
                "acknowledgement": fact,
                "updated_at": now,
            }
        )
    if record.phase == "cancel_requested" and record.pre_cancel_phase == "dispatched":
        return record.model_copy(
            update={
                "pre_cancel_phase": "accepted",
                "acknowledgement": fact,
                "updated_at": now,
            }
        )
    _fail("result_conflict")


def _apply_rejected_acknowledgement(
    record: ActionRecordV1, fact: ActionAcknowledgementV1, now: str
) -> ActionRecordV1:
    dispatched_origin = record.phase == "dispatched" or (
        record.phase == "cancel_requested" and record.pre_cancel_phase == "dispatched"
    )
    if not dispatched_origin or fact.error is None:
        _fail("result_conflict")
    acknowledged_expired = _instant(fact.acknowledged_at) >= _instant(
        record.invocation.expires_at
    )
    if (fact.error.code == "expired") != acknowledged_expired:
        _fail("result_conflict")
    return record.model_copy(
        update={
            "phase": "terminal",
            "pre_cancel_phase": None,
            "acknowledgement": fact,
            "terminal_state": "rejected",
            "error": fact.error,
            "updated_at": now,
        }
    )


def _apply_cancellation(
    record: ActionRecordV1, fact: ActionCancellationV1, now: str
) -> ActionRecordV1:
    if fact.reason == "expired":
        _fail("result_conflict")
    if _instant(fact.requested_at) < _instant(record.invocation.requested_at):
        _fail("result_conflict")
    if record.phase == "pending":
        return record.model_copy(
            update={
                "phase": "terminal",
                "cancellation": fact,
                "terminal_state": "rejected",
                "error": _cancellation_error(fact.reason),
                "updated_at": now,
            }
        )
    if record.phase not in {"dispatched", "accepted"} or record.dispatch is None:
        _fail("result_conflict")
    if _instant(fact.requested_at) < _instant(record.dispatch.dispatched_at):
        _fail("result_conflict")
    return record.model_copy(
        update={
            "phase": "cancel_requested",
            "pre_cancel_phase": record.phase,
            "cancellation": fact,
            "updated_at": now,
        }
    )


def _apply_expiry(
    record: ActionRecordV1, fact: ActionExpireFactV1, now: str
) -> ActionRecordV1:
    if record.phase == "pending":
        return record.model_copy(
            update={
                "phase": "terminal",
                "expiry": fact,
                "terminal_state": "rejected",
                "error": _error("expired"),
                "updated_at": now,
            }
        )
    if record.phase in {"dispatched", "accepted"}:
        cancellation = ActionCancellationV1(
            **_identity_dict(record.invocation), reason="expired", requested_at=now
        )
        return record.model_copy(
            update={
                "phase": "cancel_requested",
                "pre_cancel_phase": record.phase,
                "cancellation": cancellation,
                "expiry": fact,
                "updated_at": now,
            }
        )
    if record.phase == "cancel_requested":
        if record.expiry is not None:
            return record
        return record.model_copy(update={"expiry": fact, "updated_at": now})
    _fail("result_conflict")


def _apply_control(
    record: ActionRecordV1, fact: ActionControlFactV1, now: str
) -> ActionRecordV1:
    if _instant(fact.observed_at) < _instant(record.created_at):
        _fail("result_conflict")
    if record.dispatch is not None and _instant(fact.observed_at) < _instant(
        record.dispatch.dispatched_at
    ):
        _fail("result_conflict")
    terminal_state = "rejected" if record.phase == "pending" else "outcome_unknown"
    return record.model_copy(
        update={
            "phase": "terminal",
            "pre_cancel_phase": None,
            "control": fact,
            "terminal_state": terminal_state,
            "error": _error("executor_unavailable"),
            "updated_at": now,
        }
    )


def _apply_result(
    record: ActionRecordV1, fact: ActionResultV1, now: str
) -> ActionRecordV1:
    if record.phase not in {"accepted", "cancel_requested"}:
        _fail("result_conflict")
    anchor = _result_anchor(record)
    if _instant(fact.started_at) < _instant(anchor) or _instant(
        fact.started_at
    ) >= _instant(record.invocation.expires_at):
        _fail("result_conflict")
    if record.cancellation and fact.state == "interrupted_before_delivery":
        expected = _cancellation_error(record.cancellation.reason).code
        if fact.error is None or fact.error.code != expected:
            _fail("result_conflict")
    return record.model_copy(
        update={
            "phase": "terminal",
            "pre_cancel_phase": None,
            "result": fact,
            "terminal_state": fact.state,
            "error": fact.error,
            "updated_at": now,
        }
    )


def _result_anchor(record: ActionRecordV1) -> str:
    if record.acknowledgement is not None:
        return record.acknowledgement.acknowledged_at
    if record.dispatch is not None:
        return record.dispatch.dispatched_at
    _fail("result_conflict")


def _validate_record_coupling(record: ActionRecordV1) -> None:
    if record.invocation_hash != canonical_sha256(record.invocation):
        _validation_error("record invocation hash mismatch")
    if _instant(record.updated_at) < _instant(record.created_at):
        _validation_error("record update precedes creation")
    _validate_record_identities(record)
    _validate_record_timestamps(record)
    if record.phase == "terminal":
        _validate_terminal_record(record)
    else:
        _validate_active_record(record)


def _validate_record_identities(record: ActionRecordV1) -> None:
    facts = (
        record.dispatch,
        record.acknowledgement,
        record.cancellation,
        record.expiry,
        record.control,
        record.result,
    )
    if any(
        fact is not None and _identity_tuple(fact) != _identity_tuple(record.invocation)
        for fact in facts
    ):
        _validation_error("record fact identity mismatch")


def _validate_record_timestamps(record: ActionRecordV1) -> None:
    invocation = record.invocation
    requested = _instant(invocation.requested_at)
    expires = _instant(invocation.expires_at)
    created = _instant(record.created_at)
    updated = _instant(record.updated_at)
    if not requested <= created < expires:
        _validation_error("record creation time is invalid")
    facts = tuple(
        _instant(_fact_timestamp(fact))
        for fact in (
            record.dispatch,
            record.acknowledgement,
            record.cancellation,
            record.expiry,
            record.control,
            record.result,
        )
        if fact is not None
    )
    if any(timestamp > updated for timestamp in facts):
        _validation_error("record fact follows update time")
    dispatch = record.dispatch
    if dispatch is not None and not (
        requested <= _instant(dispatch.dispatched_at) < expires
    ):
        _validation_error("record dispatch time is invalid")
    acknowledgement = record.acknowledgement
    if acknowledgement is not None:
        if dispatch is None or _instant(acknowledgement.acknowledged_at) < _instant(
            dispatch.dispatched_at
        ):
            _validation_error("record acknowledgement time is invalid")
        if acknowledgement.state == "accepted" and not (
            _instant(acknowledgement.acknowledged_at) < expires
        ):
            _validation_error("accepted acknowledgement is too late")
        acknowledged_expired = _instant(acknowledgement.acknowledged_at) >= expires
        if acknowledgement.state == "rejected" and (
            (acknowledgement.error.code == "expired") != acknowledged_expired
        ):
            _validation_error("rejected acknowledgement time is invalid")
    cancellation = record.cancellation
    if cancellation is not None:
        cancelled = _instant(cancellation.requested_at)
        if cancelled < requested or (
            dispatch is not None and cancelled < _instant(dispatch.dispatched_at)
        ):
            _validation_error("record cancellation time is invalid")
        if acknowledgement is not None and not (
            _instant(acknowledgement.acknowledged_at) < cancelled
        ):
            _validation_error("record acknowledgement follows cancellation")
    expiry = record.expiry
    if expiry is not None:
        observed = _instant(expiry.observed_at)
        if observed < expires:
            _validation_error("record expiry time is invalid")
        if cancellation is not None and observed < _instant(cancellation.requested_at):
            _validation_error("record expiry precedes cancellation")
        if (
            cancellation is not None
            and cancellation.reason == "expired"
            and (cancellation.requested_at != expiry.observed_at)
        ):
            _validation_error("expiry cancellation time is invalid")
    control = record.control
    if control is not None and (
        _instant(control.observed_at) < created
        or (
            dispatch is not None
            and _instant(control.observed_at) < _instant(dispatch.dispatched_at)
        )
    ):
        _validation_error("record control time is invalid")
    result = record.result
    if result is not None:
        anchor = (
            acknowledgement.acknowledged_at
            if acknowledgement
            else (dispatch.dispatched_at if dispatch else None)
        )
        if anchor is None or not (
            _instant(anchor) <= _instant(result.started_at) < expires
        ):
            _validation_error("record result time is invalid")


def _validate_active_record(record: ActionRecordV1) -> None:
    forbidden = (
        record.control,
        record.result,
        record.terminal_state,
        record.error,
    )
    if any(item is not None for item in forbidden):
        _validation_error("non-terminal record contains terminal facts")
    if record.phase == "pending" and any(
        item is not None
        for item in (
            record.pre_cancel_phase,
            record.dispatch,
            record.acknowledgement,
            record.cancellation,
            record.expiry,
        )
    ):
        _validation_error("pending record contains later facts")
    if record.phase == "dispatched" and not (
        record.dispatch
        and record.pre_cancel_phase is None
        and record.acknowledgement is None
        and record.cancellation is None
        and record.expiry is None
    ):
        _validation_error("dispatched record coupling is invalid")
    if record.phase == "accepted" and not (
        record.dispatch
        and record.acknowledgement
        and record.acknowledgement.state == "accepted"
        and record.pre_cancel_phase is None
        and record.cancellation is None
        and record.expiry is None
    ):
        _validation_error("accepted record coupling is invalid")
    if record.phase == "cancel_requested":
        accepted = record.acknowledgement is not None
        valid = (
            record.dispatch
            and record.cancellation
            and record.pre_cancel_phase == ("accepted" if accepted else "dispatched")
            and (not accepted or record.acknowledgement.state == "accepted")
        )
        if not valid:
            _validation_error("cancel-requested record coupling is invalid")


def _validate_terminal_record(record: ActionRecordV1) -> None:
    if record.pre_cancel_phase is not None or record.terminal_state is None:
        _validation_error("terminal record coupling is invalid")
    if record.terminal_state == "delivered":
        valid = (
            _valid_result_history(record)
            and record.result is not None
            and record.result.state == "delivered"
            and record.error is None
        )
    elif record.terminal_state == "interrupted_before_delivery":
        valid = (
            _valid_result_history(record)
            and record.result is not None
            and record.result.state == "interrupted_before_delivery"
            and record.error == record.result.error
        )
    elif record.terminal_state == "outcome_unknown":
        terminal_owners = (record.control is not None, record.result is not None)
        valid = (
            sum(terminal_owners) == 1
            and (record.result is None or record.result.state == "outcome_unknown")
            and (record.result is None or _valid_result_history(record))
            and (record.control is None or _valid_control_history(record))
            and record.error is not None
            and record.error.code == "executor_unavailable"
        )
    else:
        valid = _valid_rejected_record(record)
    if not valid:
        _validation_error("terminal record coupling is invalid")


def _valid_result_history(record: ActionRecordV1) -> bool:
    if record.result is None or record.control is not None or record.dispatch is None:
        return False
    acknowledgement = record.acknowledgement
    if acknowledgement is not None and acknowledgement.state != "accepted":
        return False
    if acknowledgement is None and record.cancellation is None:
        return False
    if record.expiry is not None and record.cancellation is None:
        return False
    return not (
        record.cancellation is not None
        and record.result.state == "interrupted_before_delivery"
        and record.result.error != _cancellation_error(record.cancellation.reason)
    )


def _valid_control_history(record: ActionRecordV1) -> bool:
    if record.control is None or record.result is not None:
        return False
    if record.dispatch is None:
        return all(
            item is None
            for item in (record.acknowledgement, record.cancellation, record.expiry)
        )
    if record.acknowledgement is not None and (
        record.acknowledgement.state != "accepted"
    ):
        return False
    return record.expiry is None or record.cancellation is not None


def _valid_rejected_record(record: ActionRecordV1) -> bool:
    if record.error is None or record.result is not None:
        return False
    acknowledgement = record.acknowledgement
    if acknowledgement is not None:
        return bool(
            acknowledgement.state == "rejected"
            and record.dispatch is not None
            and record.control is None
            and record.error == acknowledgement.error
            and (record.expiry is None or record.cancellation is not None)
        )
    pending_owners = (
        record.cancellation is not None,
        record.expiry is not None,
        record.control is not None,
    )
    if record.dispatch is not None or sum(pending_owners) != 1:
        return False
    if record.cancellation is not None:
        return record.error == _cancellation_error(record.cancellation.reason)
    if record.expiry is not None:
        return record.error == _error("expired")
    return record.error == _error("executor_unavailable")
