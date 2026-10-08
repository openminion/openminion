from dataclasses import dataclass, field
import json
from typing import Any, Literal, Optional, cast

from openminion.base.config import ActionPolicyConfig
from openminion.base.config.action_policy import map_action_policy_mode
from openminion.base.redaction import redact_mapping
from openminion.base.time import utc_now_iso  # noqa: F401
from openminion.modules.tool.plugin_api import (
    ToolConfirmationPreview,
    confirmation_preview_payload,
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


def build_policy_facts(
    *,
    canonical_tool: str,
    reason_code: str = "",
    risk: dict[str, Any] | None = None,
    duration_options: list[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    facts = {
        "canonical_tool": str(canonical_tool or "").strip(),
        "reason_code": str(reason_code or "").strip(),
        "risk": dict(risk or {}),
        "duration_options": list(duration_options),
    }
    return {key: value for key, value in facts.items() if value}


def build_consent_preview(
    tool_name: str,
    args: dict[str, Any],
    *,
    max_length: int = 4096,
) -> str:
    """Return a redacted, bounded invocation preview for operator consent."""
    redacted, _ = redact_mapping(args or {})
    bounded = _bounded_preview_value(redacted)
    rendered = json.dumps(
        bounded,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )
    preview = f"{str(tool_name or '').strip()}({rendered})"
    limit = max(32, int(max_length))
    if len(preview) <= limit:
        return preview
    if "command" in redacted:
        raise ValueError("command is too long to display for informed approval")
    return f"{preview[: limit - 3]}..."


def _bounded_preview_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= 3:
        return "[TRUNCATED]"
    if isinstance(value, str):
        return value if len(value) <= 120 else f"{value[:117]}..."
    if isinstance(value, dict):
        items = list(value.items())
        bounded = {
            str(key): (
                child
                if depth == 0 and str(key) == "command" and isinstance(child, str)
                else _bounded_preview_value(child, depth=depth + 1)
            )
            for key, child in items[:8]
        }
        if len(items) > 8:
            bounded["..."] = f"{len(items) - 8} more"
        return bounded
    if isinstance(value, (list, tuple)):
        bounded_items = [
            _bounded_preview_value(item, depth=depth + 1) for item in value[:5]
        ]
        if len(value) > 5:
            bounded_items.append(f"[{len(value) - 5} more]")
        return bounded_items
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(type(value).__name__)


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
class PolicyRule:
    tool_name: str
    min_risk_class: RiskClass | Literal[""] = ""
    mode: Literal["block", "ask", "auto"] = "ask"


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
    rules: tuple[PolicyRule, ...] = ()
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


def policy_config_from_action_policy(action_policy: ActionPolicyConfig) -> PolicyConfig:
    defaults = PolicyConfig()
    return PolicyConfig(
        mode=cast(PolicyMode, map_action_policy_mode(action_policy.mode)),
        default_action=action_policy.default_action or defaults.default_action,
        allow_read_only_without_prompt=action_policy.allow_read_only_without_prompt,
        rules=tuple(
            PolicyRule(
                tool_name=str(rule.match.tool_name or "").strip(),
                min_risk_class=cast(
                    RiskClass | Literal[""],
                    str(rule.match.min_risk_class or "").strip().lower(),
                ),
                mode=cast(
                    Literal["block", "ask", "auto"],
                    str(rule.mode or "ask").strip().lower(),
                ),
            )
            for rule in action_policy.rules
        ),
        affirmative_tokens=list(
            action_policy.affirmative_tokens or defaults.affirmative_tokens
        ),
        negative_tokens=list(action_policy.negative_tokens or defaults.negative_tokens),
    )


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
    confirmation_preview: ToolConfirmationPreview | None = None

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
            "confirmation_preview": (
                None
                if self.confirmation_preview is None
                else confirmation_preview_payload(self.confirmation_preview)
            ),
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
    def __init__(
        self, code: str, message: str, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}
