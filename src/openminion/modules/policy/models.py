from openminion.base.time import utc_now_iso  # noqa: F401

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, Literal, Optional

from .constants import (
    POLICY_DECISION_REQUIRE_CONFIRM,
    POLICY_DURATION_FOREVER,
    POLICY_DURATION_ONCE,
    POLICY_MODE_CHOICES,
    POLICY_MODE_ENFORCE,
    POLICY_REVERSIBILITY_UNKNOWN,
    POLICY_RISK_READ,
    POLICY_SIDE_EFFECT_NONE,
    POLICY_SUBJECT_ID_LOCAL,
)


PolicyMode = Literal["disabled", "log_only", "enforce", "enforce_safe"]
PolicyDecisionType = Literal["ALLOW", "DENY", "REQUIRE_CONFIRM"]
GrantEffect = Literal["allow", "deny"]
DurationType = Literal["once", "until", "session", "forever"]
RiskClass = Literal[
    "read", "write", "exec", "state_change", "destructive", "financial", "security"
]
SideEffects = Literal["none", "local", "remote", "external_account"]
Reversibility = Literal["reversible", "partially_reversible", "irreversible", "unknown"]


def normalize_mode(value: str) -> PolicyMode:
    mode = str(value or "").strip().lower()
    if mode in POLICY_MODE_CHOICES:
        return mode  # type: ignore[return-value]
    raise ValueError(f"Invalid policy mode: {value}")


def stable_invocation_hash(*, tool: str, method: str, args: Dict[str, Any]) -> str:
    filtered_args = {
        key: value
        for key, value in (args or {}).items()
        if not str(key).startswith("_")
    }
    payload = {
        "tool": str(tool),
        "method": str(method),
        "args": filtered_args,
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def sanitize_args(args: Dict[str, Any]) -> Dict[str, Any]:
    sanitized: Dict[str, Any] = {}
    for key, value in (args or {}).items():
        low = key.lower()
        if any(
            token in low
            for token in ("token", "secret", "password", "key", "authorization")
        ):
            sanitized[key] = "[REDACTED]"
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            sanitized[key] = value
            continue
        if isinstance(value, (list, dict)):
            kind = "array" if isinstance(value, list) else "object"
            sanitized[key] = {"_type": kind, "size": len(value)}
            continue
        sanitized[key] = {"_type": type(value).__name__}
    return sanitized


@dataclass(frozen=True)
class RiskSpec:
    risk_class: RiskClass
    side_effects: SideEffects = POLICY_SIDE_EFFECT_NONE
    reversibility: Reversibility = POLICY_REVERSIBILITY_UNKNOWN
    default_confirm: bool = False
    sensitive_targets: list[Dict[str, Any] | str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "risk_class": self.risk_class,
            "side_effects": self.side_effects,
            "reversibility": self.reversibility,
            "default_confirm": self.default_confirm,
            "sensitive_targets": list(self.sensitive_targets),
        }

    @staticmethod
    def from_dict(payload: Dict[str, Any]) -> "RiskSpec":
        return RiskSpec(
            risk_class=str(payload.get("risk_class", POLICY_RISK_READ)),  # type: ignore[arg-type]
            side_effects=str(payload.get("side_effects", POLICY_SIDE_EFFECT_NONE)),  # type: ignore[arg-type]
            reversibility=str(
                payload.get("reversibility", POLICY_REVERSIBILITY_UNKNOWN)
            ),  # type: ignore[arg-type]
            default_confirm=bool(payload.get("default_confirm", False)),
            sensitive_targets=list(payload.get("sensitive_targets", [])),
        )


@dataclass
class PolicyConfig:
    mode: PolicyMode = POLICY_MODE_ENFORCE
    default_action: Literal["allow", "require_confirm"] = (
        POLICY_DECISION_REQUIRE_CONFIRM.lower()
    )
    default_duration: DurationType = POLICY_DURATION_ONCE
    sandbox_path_prefixes: list[str] = field(
        default_factory=lambda: ["/sandbox", "./sandbox"]
    )
    allow_read_only_without_prompt: bool = True
    affirmative_tokens: list[str] = field(
        default_factory=lambda: [
            "yes",
            "y",
            "proceed",
            "go",
            "confirm",
            "sure",
            "affirmative",
            "sounds good",
        ]
    )
    negative_tokens: list[str] = field(
        default_factory=lambda: ["no", "n", "cancel", "stop", "abort", "not now"]
    )
    subject_id_default: str = POLICY_SUBJECT_ID_LOCAL
    decision_log_enabled: bool = True


@dataclass
class PolicyGrantInput:
    effect: GrantEffect
    tool: str = "*"
    method: str = "*"
    target_json: Dict[str, Any] = field(default_factory=dict)
    duration_type: DurationType = POLICY_DURATION_FOREVER
    subject_id: str = POLICY_SUBJECT_ID_LOCAL
    expires_at: Optional[str] = None
    session_id: Optional[str] = None
    invocation_hash: Optional[str] = None
    max_uses: Optional[int] = None
    reason: Optional[str] = None
    created_trace_id: Optional[str] = None
    risk_floor: Optional[RiskClass] = None


@dataclass
class PolicyGrant:
    grant_id: str
    subject_id: str
    effect: GrantEffect
    tool: str
    method: str
    target_json: Dict[str, Any]
    duration_type: DurationType
    expires_at: Optional[str]
    session_id: Optional[str]
    invocation_hash: Optional[str]
    max_uses: Optional[int]
    uses_count: int
    created_at: str
    updated_at: str
    revoked_at: Optional[str]
    reason: Optional[str]
    created_trace_id: Optional[str]
    risk_floor: Optional[RiskClass]

    @property
    def active(self) -> bool:
        return self.revoked_at is None


@dataclass
class InvocationSummary:
    invocation_id: str
    tool: str
    method: str
    args: Dict[str, Any]
    invocation_hash: str


@dataclass
class ContextSummary:
    trace_id: Optional[str] = None
    session_id: Optional[str] = None
    agent_id: Optional[str] = None
    subject_id: Optional[str] = None
    mode_name: Optional[str] = None


@dataclass
class PolicyDecision:
    decision: PolicyDecisionType
    reason_code: str
    reason: str
    risk: RiskSpec
    matched_grant_id: Optional[str] = None
    confirm_request: Optional[Dict[str, Any]] = None
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision": self.decision,
            "reason_code": self.reason_code,
            "reason": self.reason,
            "risk": self.risk.to_dict(),
            "matched_grant_id": self.matched_grant_id,
            "confirm_request": self.confirm_request,
            "details": dict(self.details),
        }
