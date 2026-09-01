from openminion.base.time import utc_now_iso  # noqa: F401

from dataclasses import dataclass, field
from typing import Any, Literal, Optional, cast

from openminion.modules.tool.plugin_api import (
    stable_invocation_hash as stable_invocation_hash,
)

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
        return cast(PolicyMode, mode)
    raise ValueError(f"Invalid policy mode: {value}")


def sanitize_args(args: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in (args or {}).items():
        low = key.lower()
        if any(
            token in low
            for token in ("token", "secret", "password", "key", "authorization")
        ):
            sanitized[key] = "[REDACTED]"
        elif isinstance(value, (str, int, float, bool)) or value is None:
            sanitized[key] = value
        elif isinstance(value, (list, dict)):
            kind = "array" if isinstance(value, list) else "object"
            sanitized[key] = {"_type": kind, "size": len(value)}
        else:
            sanitized[key] = {"_type": type(value).__name__}
    return sanitized


@dataclass(frozen=True)
class RiskSpec:
    risk_class: RiskClass
    side_effects: SideEffects = POLICY_SIDE_EFFECT_NONE
    reversibility: Reversibility = POLICY_REVERSIBILITY_UNKNOWN
    default_confirm: bool = False
    sensitive_targets: list[dict[str, Any] | str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk_class": self.risk_class,
            "side_effects": self.side_effects,
            "reversibility": self.reversibility,
            "default_confirm": self.default_confirm,
            "sensitive_targets": list(self.sensitive_targets),
        }

    @staticmethod
    def from_dict(payload: dict[str, Any]) -> "RiskSpec":
        return RiskSpec(
            risk_class=cast(
                RiskClass,
                str(payload.get("risk_class", POLICY_RISK_READ)),
            ),
            side_effects=cast(
                SideEffects,
                str(payload.get("side_effects", POLICY_SIDE_EFFECT_NONE)),
            ),
            reversibility=cast(
                Reversibility,
                str(payload.get("reversibility", POLICY_REVERSIBILITY_UNKNOWN)),
            ),
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
    target_json: dict[str, Any] = field(default_factory=dict)
    duration_type: DurationType = POLICY_DURATION_FOREVER
    subject_id: str = POLICY_SUBJECT_ID_LOCAL
    expires_at: Optional[str] = None
    session_id: Optional[str] = None
    invocation_hash: Optional[str] = None
    max_uses: Optional[int] = None
    reason: Optional[str] = None
    created_trace_id: Optional[str] = None
    risk_floor: Optional[RiskClass] = None
    approval_id: Optional[str] = None


@dataclass
class PolicyGrant:
    grant_id: str
    subject_id: str
    effect: GrantEffect
    tool: str
    method: str
    target_json: dict[str, Any]
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
    approval_id: Optional[str] = None

    @property
    def active(self) -> bool:
        return self.revoked_at is None


@dataclass
class InvocationSummary:
    invocation_id: str
    tool: str
    method: str
    args: dict[str, Any]
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
    confirm_request: Optional[dict[str, Any]] = None
    details: dict[str, Any] = field(default_factory=dict)
    approval_id: Optional[str] = None
    invocation_hash: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "reason_code": self.reason_code,
            "reason": self.reason,
            "risk": self.risk.to_dict(),
            "matched_grant_id": self.matched_grant_id,
            "confirm_request": self.confirm_request,
            "details": dict(self.details),
            "approval_id": self.approval_id,
            "invocation_hash": self.invocation_hash,
        }


@dataclass(frozen=True)
class PendingPolicyConfirmation:
    approval_id: str
    subject_id: str
    tool: str
    method: str
    invocation_hash: str
    invocation_id: str
    trace_id: Optional[str]
    session_id: Optional[str]
    preview: dict[str, Any]
    state: str
    resolution_action: Optional[str]
    grant_id: Optional[str]
    created_at: str
    expires_at: str
    resolved_at: Optional[str]


class PolicyControlError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
