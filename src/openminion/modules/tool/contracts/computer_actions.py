"""Pure versioned contracts for prospective desktop computer actions.

This module deliberately owns no tool registration, transport, runtime state,
timer, operating-system integration, or native execution.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
from typing import Annotated, Literal, Never, TypeAlias, cast

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
    model_serializer,
)

SCHEMA_VERSION: Literal[1] = 1
_HEX_48 = re.compile(r"^[0-9a-f]{48}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")

Risk: TypeAlias = Literal["low", "medium", "high", "critical"]
Capability: TypeAlias = Literal[
    "computer.pointer",
    "computer.click",
    "computer.scroll",
    "computer.keyboard",
    "computer.wait",
]
ActionKind: TypeAlias = Literal["pointer_move", "click", "scroll", "key_press", "wait"]
CancellationReason: TypeAlias = Literal[
    "user_stop",
    "session_cancel",
    "grant_revoked",
    "permission_revoked",
    "source_changed",
    "app_shutdown",
    "expired",
]
GrantRevocationReason: TypeAlias = Literal[
    "user_stop",
    "session_cancel",
    "daemon_disconnect",
    "permission_revoked",
    "source_changed",
    "app_shutdown",
    "expired",
]
ErrorCode: TypeAlias = Literal[
    "invalid_action",
    "unsupported_action",
    "approval_required",
    "approval_mismatch",
    "grant_required",
    "grant_revoked",
    "permission_revoked",
    "frame_stale",
    "target_changed",
    "executor_unavailable",
    "expired",
    "replay_conflict",
    "cancelled",
    "result_conflict",
]

ERROR_MESSAGES: dict[str, str] = {
    "invalid_action": "The action request is invalid.",
    "unsupported_action": "The action is not supported.",
    "approval_required": "Current approval is required.",
    "approval_mismatch": "The approval does not match the action.",
    "grant_required": "A current desktop grant is required.",
    "grant_revoked": "The desktop grant is not active.",
    "permission_revoked": "The required system permission is not active.",
    "frame_stale": "The grounded frame is no longer current.",
    "target_changed": "The action target has changed.",
    "executor_unavailable": "The desktop executor is unavailable.",
    "expired": "The action has expired.",
    "replay_conflict": "The action identity conflicts with an earlier request.",
    "cancelled": "The action was cancelled.",
    "result_conflict": "The action result conflicts with terminal state.",
}

_CAPABILITY_BY_ACTION: dict[str, str] = {
    "pointer_move": "computer.pointer",
    "click": "computer.click",
    "scroll": "computer.scroll",
    "key_press": "computer.keyboard",
    "wait": "computer.wait",
}
_RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}
_IDENTITY_FIELDS = (
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
_KEY_TOKENS = {
    "backspace",
    "delete",
    "end",
    "enter",
    "escape",
    "home",
    "page_down",
    "page_up",
    "space",
    "tab",
    "arrow_down",
    "arrow_left",
    "arrow_right",
    "arrow_up",
}
_MODIFIERS = {"alt", "control", "meta", "shift"}


def _validation_error(message: str, cause: Exception | None = None) -> Never:
    raise ValueError(message) from cause  # allow-bare-raise: schema validation


def _identity(value: str, maximum: int) -> str:
    if not isinstance(value, str):
        _validation_error("identity must be a string")
    encoded = value.encode("utf-8")
    if (
        not encoded
        or not value.strip()
        or len(encoded) > maximum
        or _CONTROL.search(value)
    ):
        _validation_error("identity is outside its admitted bound")
    return value


def _hex_48(value: str) -> str:
    if not isinstance(value, str) or not _HEX_48.fullmatch(value):
        _validation_error("expected 48 lowercase hexadecimal characters")
    return value


def _hex_64(value: str) -> str:
    if not isinstance(value, str) or not _HEX_64.fullmatch(value):
        _validation_error("expected 64 lowercase hexadecimal characters")
    return value


def _timestamp(value: str) -> str:
    if not isinstance(value, str) or not _TIMESTAMP.fullmatch(value):
        _validation_error("expected canonical UTC RFC 3339 milliseconds")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError as exc:
        _validation_error("invalid timestamp", exc)
    if parsed.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z" != value:
        _validation_error("non-canonical timestamp")
    return value


Identity128 = Annotated[str, AfterValidator(partial(_identity, maximum=128))]
Identity256 = Annotated[str, AfterValidator(partial(_identity, maximum=256))]
Hex48 = Annotated[str, AfterValidator(_hex_48)]
Hex64 = Annotated[str, AfterValidator(_hex_64)]
Timestamp = Annotated[str, AfterValidator(_timestamp)]


def _immutable_sequence(value: object) -> object:
    return tuple(value) if isinstance(value, list) else value


StringTuple = Annotated[tuple[str, ...], BeforeValidator(_immutable_sequence)]
CapabilityTuple = Annotated[
    tuple[Capability, ...], BeforeValidator(_immutable_sequence)
]
ActionKindTuple = Annotated[
    tuple[ActionKind, ...], BeforeValidator(_immutable_sequence)
]


def _instant(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
        tzinfo=timezone.utc
    )


def _milliseconds(start: str, end: str) -> int:
    return int((_instant(end) - _instant(start)).total_seconds() * 1000)


class ActionErrorV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    code: ErrorCode
    message: str

    @model_validator(mode="after")
    def _fixed_message(self) -> ActionErrorV1:
        if self.message != ERROR_MESSAGES[self.code]:
            _validation_error("error message does not match its code")
        return self


class ComputerActionContractError(ValueError):
    """Fixed safe contract failure without rejected payload reflection."""

    def __init__(self, code: ErrorCode) -> None:
        self.error = ActionErrorV1(code=code, message=ERROR_MESSAGES[code])
        super().__init__(self.error.message)

    @property
    def code(self) -> ErrorCode:
        return self.error.code


def _fail(code: ErrorCode) -> Never:
    raise ComputerActionContractError(code)


class ActionTargetV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    capture_id: Hex48
    frame_id: Hex48
    source_kind: Literal["display", "region"]
    parent_source_kind: Literal["display"]
    frame_width: int = Field(ge=64, le=7680)
    frame_height: int = Field(ge=64, le=7680)
    scale_millis: int = Field(ge=500, le=4000)
    frame_captured_at: Timestamp
    frame_expires_at: Timestamp

    @model_validator(mode="after")
    def _ordered(self) -> ActionTargetV1:
        if _instant(self.frame_captured_at) >= _instant(self.frame_expires_at):
            _validation_error("frame expiry must follow capture")
        return self


class ComputerActionV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    kind: ActionKind
    x_bps: int | None = Field(default=None, ge=0, le=10000)
    y_bps: int | None = Field(default=None, ge=0, le=10000)
    duration_ms: int | None = Field(default=None, ge=0, le=5000)
    button: Literal["left", "right", "middle"] | None = None
    count: Literal[1, 2] | None = None
    delta_x_bps: int | None = Field(default=None, ge=-10000, le=10000)
    delta_y_bps: int | None = Field(default=None, ge=-10000, le=10000)
    keys: StringTuple | None = None
    modifiers: StringTuple | None = None

    @model_validator(mode="after")
    def _coupled(self) -> ComputerActionV1:
        required = {
            "pointer_move": {"kind", "x_bps", "y_bps", "duration_ms"},
            "click": {"kind", "x_bps", "y_bps", "button", "count"},
            "scroll": {"kind", "delta_x_bps", "delta_y_bps"},
            "key_press": {"kind", "keys", "modifiers"},
            "wait": {"kind", "duration_ms"},
        }[self.kind]
        if self.model_fields_set != required:
            _validation_error("action keys do not match its kind")
        if self.kind == "pointer_move" and cast(int, self.duration_ms) > 2000:
            _validation_error("pointer duration exceeds its bound")
        if self.kind == "wait" and cast(int, self.duration_ms) < 1:
            _validation_error("wait duration must be positive")
        if self.kind == "scroll" and not (
            cast(int, self.delta_x_bps) or cast(int, self.delta_y_bps)
        ):
            _validation_error("scroll delta cannot be zero")
        if self.kind == "key_press":
            keys = cast(tuple[str, ...], self.keys)
            modifiers = cast(tuple[str, ...], self.modifiers)
            if not 1 <= len(keys) <= 4 or any(not _valid_key(key) for key in keys):
                _validation_error("invalid key token")
            if tuple(sorted(set(modifiers))) != modifiers or any(
                item not in _MODIFIERS for item in modifiers
            ):
                _validation_error("invalid modifier list")
        return self

    @model_serializer(mode="plain")
    def _serialize(self) -> dict[str, object]:
        return {name: getattr(self, name) for name in self.model_fields_set}


def _valid_key(value: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9]", value)) or value in _KEY_TOKENS


class ActionIntentV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1]
    action: ComputerActionV1
    requested_risk: Risk


@dataclass(frozen=True)
class AppliedApprovalFactV1:
    approval_id: str
    session_id: str
    trace_id: str
    tool_call_id: str
    actor_id: str
    state: Literal["applied"]


@dataclass(frozen=True)
class TrustedCaptureFrameV1:
    session_id: str
    capture_id: str
    frame_id: str
    source_kind: str
    parent_source_kind: str
    frame_width: int
    frame_height: int
    scale_factor: float | None
    captured_at: str
    expires_at: str
    observing: bool


@dataclass(frozen=True)
class TrustedActionContextV1:
    action_id: str
    idempotency_key: str
    daemon_id: str
    desktop_client_id: str
    user_id: str
    session_id: str
    trace_id: str
    turn_id: str
    tool_call_id: str
    actor_id: str
    actor_kind: Literal["agent"]
    target: ActionTargetV1
    requested_at: str
    expires_at: str
    approval: AppliedApprovalFactV1 | None
    registered_risk_floor: Literal["high"]


class ActionInvocationV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1]
    action_id: Hex48
    idempotency_key: Hex64
    daemon_id: Identity128
    desktop_client_id: Identity128
    user_id: Identity128
    session_id: Identity256
    trace_id: Identity128
    turn_id: Identity128
    tool_call_id: Identity128
    actor_id: Identity128
    actor_kind: Literal["agent"]
    capability: Capability
    target: ActionTargetV1
    action: ComputerActionV1
    requested_at: Timestamp
    expires_at: Timestamp
    requested_risk: Risk
    effective_risk: Literal["high", "critical"]
    approval_mode: Literal["approve_each"]
    approval_id: Identity128

    @model_validator(mode="after")
    def _coupled(self) -> ActionInvocationV1:
        if self.capability != _CAPABILITY_BY_ACTION[self.action.kind]:
            _validation_error("capability does not match action")
        expected_risk = "critical" if self.requested_risk == "critical" else "high"
        if self.effective_risk != expected_risk:
            _validation_error("effective risk lowered the registered floor")
        requested = _instant(self.requested_at)
        expires = _instant(self.expires_at)
        if (
            not requested < expires
            or _milliseconds(self.requested_at, self.expires_at) > 5000
        ):
            _validation_error("action expiry is invalid")
        if not (
            _instant(self.target.frame_captured_at)
            < requested
            < _instant(self.target.frame_expires_at)
        ):
            _validation_error("frame is not current at request time")
        if _milliseconds(self.target.frame_captured_at, self.requested_at) > 30000:
            _validation_error("frame is stale")
        if expires > _instant(self.target.frame_expires_at):
            _validation_error("action outlives its frame")
        return self


class CapabilityStateV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1]
    daemon_id: Identity128
    desktop_client_id: Identity128
    user_id: Identity128
    session_id: Identity256
    capability: Capability
    availability: Literal["unavailable", "available", "revoked"]
    reason: Literal[
        "available",
        "unsupported",
        "permission_missing",
        "permission_revoked",
        "user_paused",
        "session_closed",
        "client_disconnected",
    ]
    observed_at: Timestamp

    @model_validator(mode="after")
    def _coupled(self) -> CapabilityStateV1:
        admitted = {
            "available": {"available"},
            "unavailable": {
                "unsupported",
                "permission_missing",
                "user_paused",
                "session_closed",
                "client_disconnected",
            },
            "revoked": {
                "permission_revoked",
                "user_paused",
                "session_closed",
                "client_disconnected",
            },
        }
        if self.reason not in admitted[self.availability]:
            _validation_error("capability state and reason conflict")
        return self


class DesktopGrantV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1]
    grant_id: Hex48
    daemon_id: Identity128
    desktop_client_id: Identity128
    user_id: Identity128
    session_id: Identity256
    actor_id: Identity128
    actor_kind: Literal["agent"]
    capabilities: CapabilityTuple
    action_kinds: ActionKindTuple
    target: ActionTargetV1
    issued_at: Timestamp
    expires_at: Timestamp
    state: Literal["active", "revoked", "expired"]
    revoked_at: Timestamp | None
    revocation_reason: GrantRevocationReason | None

    @model_validator(mode="after")
    def _coupled(self) -> DesktopGrantV1:
        if (
            not self.capabilities
            or tuple(sorted(set(self.capabilities))) != self.capabilities
        ):
            _validation_error("capabilities must be sorted and unique")
        if (
            not self.action_kinds
            or tuple(sorted(set(self.action_kinds))) != self.action_kinds
        ):
            _validation_error("action kinds must be sorted and unique")
        mapped = tuple(
            sorted(_CAPABILITY_BY_ACTION[kind] for kind in self.action_kinds)
        )
        if mapped != self.capabilities:
            _validation_error("grant action and capability sets conflict")
        lifetime = _milliseconds(self.issued_at, self.expires_at)
        if not 1 <= lifetime <= 30000 or _instant(self.expires_at) > _instant(
            self.target.frame_expires_at
        ):
            _validation_error("grant lifetime is invalid")
        if self.state == "active" and (
            self.revoked_at is not None or self.revocation_reason is not None
        ):
            _validation_error("active grant has revocation facts")
        if self.state == "revoked" and not (
            self.revoked_at is not None
            and _instant(self.issued_at)
            <= _instant(self.revoked_at)
            < _instant(self.expires_at)
            and self.revocation_reason not in {None, "expired"}
        ):
            _validation_error("revoked grant is invalid")
        if self.state == "expired" and not (
            self.revoked_at is not None
            and _instant(self.revoked_at) >= _instant(self.expires_at)
            and self.revocation_reason == "expired"
        ):
            _validation_error("expired grant is invalid")
        return self


class ActionAcknowledgementV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1]
    action_id: Hex48
    idempotency_key: Hex64
    daemon_id: Identity128
    desktop_client_id: Identity128
    user_id: Identity128
    session_id: Identity256
    trace_id: Identity128
    turn_id: Identity128
    tool_call_id: Identity128
    actor_id: Identity128
    state: Literal["accepted", "rejected"]
    acknowledged_at: Timestamp
    error: ActionErrorV1 | None

    @model_validator(mode="after")
    def _coupled(self) -> ActionAcknowledgementV1:
        admitted = {
            "grant_required",
            "grant_revoked",
            "permission_revoked",
            "frame_stale",
            "target_changed",
            "executor_unavailable",
            "expired",
        }
        if self.state == "accepted" and self.error is not None:
            _validation_error("accepted acknowledgement has an error")
        if self.state == "rejected" and (
            self.error is None or self.error.code not in admitted
        ):
            _validation_error("rejected acknowledgement lacks an admitted error")
        return self


class ActionCancellationV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1]
    action_id: Hex48
    idempotency_key: Hex64
    daemon_id: Identity128
    desktop_client_id: Identity128
    user_id: Identity128
    session_id: Identity256
    trace_id: Identity128
    turn_id: Identity128
    tool_call_id: Identity128
    actor_id: Identity128
    reason: CancellationReason
    requested_at: Timestamp


class ActionDispatchFactV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1]
    action_id: Hex48
    idempotency_key: Hex64
    daemon_id: Identity128
    desktop_client_id: Identity128
    user_id: Identity128
    session_id: Identity256
    trace_id: Identity128
    turn_id: Identity128
    tool_call_id: Identity128
    actor_id: Identity128
    kind: Literal["dispatched"]
    dispatched_at: Timestamp


class ActionExpireFactV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1]
    action_id: Hex48
    idempotency_key: Hex64
    daemon_id: Identity128
    desktop_client_id: Identity128
    user_id: Identity128
    session_id: Identity256
    trace_id: Identity128
    turn_id: Identity128
    tool_call_id: Identity128
    actor_id: Identity128
    kind: Literal["expire"]
    observed_at: Timestamp


class ActionControlFactV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1]
    action_id: Hex48
    idempotency_key: Hex64
    daemon_id: Identity128
    desktop_client_id: Identity128
    user_id: Identity128
    session_id: Identity256
    trace_id: Identity128
    turn_id: Identity128
    tool_call_id: Identity128
    actor_id: Identity128
    kind: Literal["disconnect", "restart", "executor_unavailable"]
    observed_at: Timestamp


class ActionResultV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1]
    action_id: Hex48
    idempotency_key: Hex64
    daemon_id: Identity128
    desktop_client_id: Identity128
    user_id: Identity128
    session_id: Identity256
    trace_id: Identity128
    turn_id: Identity128
    tool_call_id: Identity128
    actor_id: Identity128
    state: Literal["interrupted_before_delivery", "delivered", "outcome_unknown"]
    started_at: Timestamp
    ended_at: Timestamp
    duration_ms: int = Field(ge=0, le=30000)
    post_action_frame_id: Hex48 | None
    error: ActionErrorV1 | None

    @model_validator(mode="after")
    def _coupled(self) -> ActionResultV1:
        if _milliseconds(self.started_at, self.ended_at) != self.duration_ms:
            _validation_error("result duration does not match timestamps")
        if self.state == "delivered":
            if self.error is not None:
                _validation_error("delivered result has an error")
        elif self.post_action_frame_id is not None:
            _validation_error("only delivered results carry a post-action frame")
        if self.state == "interrupted_before_delivery" and (
            self.error is None
            or self.error.code
            not in {
                "cancelled",
                "grant_revoked",
                "permission_revoked",
                "frame_stale",
                "target_changed",
                "expired",
                "executor_unavailable",
            }
        ):
            _validation_error("interrupted result has an invalid error")
        if self.state == "outcome_unknown" and (
            self.error is None or self.error.code != "executor_unavailable"
        ):
            _validation_error("unknown result must be executor unavailable")
        return self


FactV1: TypeAlias = (
    ActionDispatchFactV1
    | ActionExpireFactV1
    | ActionAcknowledgementV1
    | ActionCancellationV1
    | ActionResultV1
    | ActionControlFactV1
)


def parse_action_intent(payload: str | bytes | Mapping[str, object]) -> ActionIntentV1:
    try:
        value = (
            _decode_json(payload)
            if isinstance(payload, (str, bytes))
            else dict(payload)
        )
        action = value.get("action")
        kind = action.get("kind") if isinstance(action, Mapping) else None
        if kind in {"text_entry", "computer.text"}:
            _fail("unsupported_action")
        if isinstance(kind, str) and kind not in _CAPABILITY_BY_ACTION:
            _fail("unsupported_action")
        _validate_json_value(value)
        return ActionIntentV1.model_validate(value)
    except ComputerActionContractError:
        raise
    except (UnicodeError, ValueError, TypeError, ValidationError, json.JSONDecodeError):
        pass
    _fail("invalid_action")


def bind_action_target(
    frame: TrustedCaptureFrameV1,
    *,
    session_id: str,
    capture_id: str,
    frame_id: str,
    requested_at: str,
) -> ActionTargetV1:
    if not frame.observing or (frame.session_id, frame.capture_id, frame.frame_id) != (
        session_id,
        capture_id,
        frame_id,
    ):
        _fail("target_changed")
    if (
        frame.source_kind not in {"display", "region"}
        or frame.parent_source_kind != "display"
    ):
        _fail("unsupported_action")
    scale = frame.scale_factor
    if (
        scale is None
        or isinstance(scale, bool)
        or not isinstance(scale, (int, float))
        or not math.isfinite(scale)
        or not 0.5 <= scale <= 4.0
    ):
        _fail("unsupported_action")
    target: ActionTargetV1 | None = None
    try:
        target = ActionTargetV1(
            capture_id=frame.capture_id,
            frame_id=frame.frame_id,
            source_kind=cast(Literal["display", "region"], frame.source_kind),
            parent_source_kind="display",
            frame_width=frame.frame_width,
            frame_height=frame.frame_height,
            scale_millis=math.floor(scale * 1000 + 0.5),
            frame_captured_at=frame.captured_at,
            frame_expires_at=frame.expires_at,
        )
        _timestamp(requested_at)
    except (TypeError, ValueError, ValidationError):
        target = None
    if target is None:
        _fail("invalid_action")
    if (
        not _instant(target.frame_captured_at)
        < _instant(requested_at)
        < _instant(target.frame_expires_at)
        or _milliseconds(target.frame_captured_at, requested_at) > 30000
    ):
        _fail("frame_stale")
    return target


def build_action_invocation(
    intent: ActionIntentV1 | Mapping[str, object], context: TrustedActionContextV1
) -> ActionInvocationV1:
    parsed = (
        intent if isinstance(intent, ActionIntentV1) else parse_action_intent(intent)
    )
    context_valid = True
    try:
        _validate_trusted_context(context)
    except (UnicodeError, ValueError, TypeError, ValidationError):
        context_valid = False
    if not context_valid:
        _fail("invalid_action")
    approval = context.approval
    if approval is None:
        _fail("approval_required")
    if not _approval_matches(approval, context):
        _fail("approval_mismatch")
    effective: Literal["high", "critical"] = (
        "critical" if parsed.requested_risk == "critical" else "high"
    )
    invocation: ActionInvocationV1 | None = None
    try:
        invocation = ActionInvocationV1(
            schema_version=SCHEMA_VERSION,
            action_id=context.action_id,
            idempotency_key=context.idempotency_key,
            daemon_id=context.daemon_id,
            desktop_client_id=context.desktop_client_id,
            user_id=context.user_id,
            session_id=context.session_id,
            trace_id=context.trace_id,
            turn_id=context.turn_id,
            tool_call_id=context.tool_call_id,
            actor_id=context.actor_id,
            actor_kind=context.actor_kind,
            capability=cast(Capability, _CAPABILITY_BY_ACTION[parsed.action.kind]),
            target=context.target,
            action=parsed.action,
            requested_at=context.requested_at,
            expires_at=context.expires_at,
            requested_risk=parsed.requested_risk,
            effective_risk=effective,
            approval_mode="approve_each",
            approval_id=approval.approval_id,
        )
    except ValidationError:
        pass
    if invocation is None:
        _fail("invalid_action")
    return invocation


def match_desktop_grant(
    invocation: ActionInvocationV1, grant: DesktopGrantV1 | None, now: str
) -> DesktopGrantV1:
    _timestamp_or_fail(now)
    if grant is None:
        _fail("grant_required")
    expected = (
        invocation.daemon_id,
        invocation.desktop_client_id,
        invocation.user_id,
        invocation.session_id,
        invocation.actor_id,
        invocation.actor_kind,
        invocation.target,
    )
    actual = (
        grant.daemon_id,
        grant.desktop_client_id,
        grant.user_id,
        grant.session_id,
        grant.actor_id,
        grant.actor_kind,
        grant.target,
    )
    if expected != actual:
        _fail("grant_required")
    if (
        invocation.capability not in grant.capabilities
        or invocation.action.kind not in grant.action_kinds
    ):
        _fail("grant_required")
    if grant.state != "active" or not (
        _instant(grant.issued_at) <= _instant(now) < _instant(grant.expires_at)
    ):
        _fail("grant_revoked")
    if _instant(now) >= _instant(invocation.expires_at):
        _fail("expired")
    return grant


def canonical_json(value: BaseModel | Mapping[str, object] | Sequence[object]) -> str:
    """Return canonical JSON for the integer-only admitted contract domain."""

    raw: object
    if isinstance(value, BaseModel):
        raw = value.model_dump(mode="json", exclude_none=False)
    else:
        raw = value
    _validate_json_value(raw)
    return json.dumps(
        raw,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_sha256(value: BaseModel | Mapping[str, object]) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _decode_json(payload: str | bytes) -> dict[str, object]:
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", errors="strict")

    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                _validation_error("duplicate JSON key")
            result[key] = value
        return result

    value = json.loads(payload, object_pairs_hook=pairs)
    if not isinstance(value, dict):
        _validation_error("intent must be an object")
    return cast(dict[str, object], value)


def _validate_json_value(value: object) -> None:
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        _validation_error("floating point values are not admitted")
    if isinstance(value, str):
        value.encode("utf-8", errors="strict")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                _validation_error("JSON object keys must be strings")
            _validate_json_value(item)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            _validate_json_value(item)
        return
    _validation_error("value is outside the JSON contract")


def _validate_trusted_context(context: TrustedActionContextV1) -> None:
    _hex_48(context.action_id)
    _hex_64(context.idempotency_key)
    for value in (
        context.daemon_id,
        context.desktop_client_id,
        context.user_id,
        context.trace_id,
        context.turn_id,
        context.tool_call_id,
        context.actor_id,
    ):
        _identity(value, 128)
    _identity(context.session_id, 256)
    if context.actor_kind != "agent" or context.registered_risk_floor != "high":
        _validation_error("trusted policy facts are invalid")
    _timestamp(context.requested_at)
    _timestamp(context.expires_at)


def _approval_matches(
    approval: AppliedApprovalFactV1, context: TrustedActionContextV1
) -> bool:
    try:
        _identity(approval.approval_id, 128)
    except (UnicodeError, ValueError, TypeError):
        return False
    return approval.state == "applied" and (
        approval.session_id,
        approval.trace_id,
        approval.tool_call_id,
        approval.actor_id,
    ) == (
        context.session_id,
        context.trace_id,
        context.tool_call_id,
        context.actor_id,
    )


def _identity_tuple(value: ActionInvocationV1 | FactV1) -> tuple[object, ...]:
    return tuple(getattr(value, key) for key in _IDENTITY_FIELDS)


def _timestamp_or_fail(value: str) -> None:
    valid = True
    try:
        _timestamp(value)
    except (ValueError, TypeError):
        valid = False
    if not valid:
        _fail("result_conflict")


def _cancellation_error(reason: CancellationReason) -> ActionErrorV1:
    code: ErrorCode
    if reason in {"user_stop", "session_cancel", "app_shutdown"}:
        code = "cancelled"
    elif reason == "source_changed":
        code = "target_changed"
    else:
        code = cast(ErrorCode, reason)
    return _error(code)


def _error(code: ErrorCode) -> ActionErrorV1:
    return ActionErrorV1(code=code, message=ERROR_MESSAGES[code])


def _identity_dict(value: ActionInvocationV1) -> dict[str, object]:
    return {key: getattr(value, key) for key in _IDENTITY_FIELDS}
