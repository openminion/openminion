from typing import Any

from openminion.modules.tool.contracts.schemas import TOOL_ERROR_CONFIRM_REQUIRED

from ..constants import (
    POLICY_DECISION_ALLOW,
    POLICY_DECISION_DENY,
    POLICY_DECISION_REQUIRE_CONFIRM,
    POLICY_GRANT_EFFECT_ALLOW,
    POLICY_GRANT_EFFECT_DENY,
    POLICY_RISK_DESTRUCTIVE,
    POLICY_RISK_EXEC,
    POLICY_RISK_FINANCIAL,
    POLICY_RISK_READ,
    POLICY_RISK_SECURITY,
    POLICY_RISK_STATE_CHANGE,
    POLICY_REVERSIBILITY_IRREVERSIBLE,
    POLICY_REVERSIBILITY_PARTIALLY_REVERSIBLE,
    POLICY_REVERSIBILITY_REVERSIBLE,
    POLICY_REVERSIBILITY_UNKNOWN,
    POLICY_SIDE_EFFECT_EXTERNAL_ACCOUNT,
    POLICY_SIDE_EFFECT_LOCAL,
    POLICY_SIDE_EFFECT_NONE,
    POLICY_SIDE_EFFECT_REMOTE,
    POLICY_SIDE_EFFECTS,
)
from ..models import RiskSpec
from .service import PolicyCtl


try:  # pragma: no cover - optional dependency at runtime
    from openminion.modules.tool.plugin_contract import (
        PolicyDecision as ToolPolicyDecision,
    )
except ModuleNotFoundError:  # pragma: no cover
    ToolPolicyDecision = None  # type: ignore[assignment]


class PolicyToolHook:
    """Bridge policy decisions into the tool runtime policy hook."""

    def __init__(self, policyctl: PolicyCtl) -> None:
        self._policyctl = policyctl

    def check(self, *, invocation: Any, ctx: Any, capabilities: Any) -> Any:
        risk = _risk_from_tool(invocation=invocation, capabilities=capabilities)
        decision = self._policyctl.check(
            invocation=invocation, ctx=ctx, risk_override=risk
        )

        details = dict(decision.details)
        details["reason_code"] = decision.reason_code
        details["risk"] = decision.risk.to_dict()
        if decision.confirm_request is not None:
            details["confirm_request"] = decision.confirm_request

        action = {
            POLICY_DECISION_ALLOW: POLICY_GRANT_EFFECT_ALLOW,
            POLICY_DECISION_DENY: POLICY_GRANT_EFFECT_DENY,
        }.get(decision.decision, POLICY_DECISION_REQUIRE_CONFIRM.lower())
        code = {
            POLICY_DECISION_ALLOW: "POLICY_ALLOW",
            POLICY_DECISION_DENY: "POLICY_DENIED",
        }.get(decision.decision, TOOL_ERROR_CONFIRM_REQUIRED)

        if ToolPolicyDecision is not None:
            return ToolPolicyDecision(
                action=action, reason=decision.reason, code=code, details=details
            )
        return _FallbackPolicyDecision(
            action=action, reason=decision.reason, code=code, details=details
        )


class _FallbackPolicyDecision:
    def __init__(
        self, *, action: str, reason: str, code: str, details: dict[str, Any]
    ) -> None:
        self.action = action
        self.reason = reason
        self.code = code
        self.details = details


def _side_effect_class(value: Any, *, default: str) -> str:
    text = str(value)
    return text if text in POLICY_SIDE_EFFECTS else default


def _risk_from_tool(*, invocation: Any, capabilities: Any) -> RiskSpec:
    method = str(getattr(invocation, "method", "")).lower()
    tool = str(getattr(invocation, "tool", "")).lower()
    side_effects = str(getattr(capabilities, "side_effects", POLICY_SIDE_EFFECT_NONE))
    risk_level = str(getattr(capabilities, "risk_level", "low"))

    if any(token in method for token in ("delete", "remove", "rm", "destroy", "kill")):
        return RiskSpec(
            risk_class=POLICY_RISK_DESTRUCTIVE,
            side_effects=POLICY_SIDE_EFFECT_LOCAL,
            reversibility=POLICY_REVERSIBILITY_IRREVERSIBLE,
            default_confirm=True,
        )

    if any(token in method for token in ("transfer", "buy", "pay", "charge")):
        return RiskSpec(
            risk_class=POLICY_RISK_FINANCIAL,
            side_effects=POLICY_SIDE_EFFECT_EXTERNAL_ACCOUNT,
            reversibility=POLICY_REVERSIBILITY_UNKNOWN,
            default_confirm=True,
        )

    if any(token in method for token in ("exec", "run", "shell")):
        return RiskSpec(
            risk_class=POLICY_RISK_EXEC,
            side_effects=_side_effect_class(
                side_effects, default=POLICY_SIDE_EFFECT_LOCAL
            ),  # type: ignore[arg-type]
            reversibility=POLICY_REVERSIBILITY_UNKNOWN,
            default_confirm=True,
        )

    if any(
        token in method
        for token in ("click", "submit", "apply", "restart", "start", "stop")
    ):
        return RiskSpec(
            risk_class=POLICY_RISK_STATE_CHANGE,
            side_effects=_side_effect_class(
                side_effects, default=POLICY_SIDE_EFFECT_LOCAL
            ),  # type: ignore[arg-type]
            reversibility=POLICY_REVERSIBILITY_PARTIALLY_REVERSIBLE,
            default_confirm=True,
        )

    if risk_level == "high":
        return RiskSpec(
            risk_class=POLICY_RISK_SECURITY,
            side_effects=_side_effect_class(
                side_effects, default=POLICY_SIDE_EFFECT_REMOTE
            ),  # type: ignore[arg-type]
            reversibility=POLICY_REVERSIBILITY_UNKNOWN,
            default_confirm=True,
        )

    if side_effects != POLICY_SIDE_EFFECT_NONE:
        return RiskSpec(
            risk_class=POLICY_RISK_STATE_CHANGE,
            side_effects=_side_effect_class(
                side_effects, default=POLICY_SIDE_EFFECT_LOCAL
            ),  # type: ignore[arg-type]
            reversibility=POLICY_REVERSIBILITY_PARTIALLY_REVERSIBLE,
            default_confirm=True,
        )

    if any(token in tool for token in ("fs", "journal", "log", "read")):
        return RiskSpec(
            risk_class=POLICY_RISK_READ,
            side_effects=POLICY_SIDE_EFFECT_NONE,
            reversibility=POLICY_REVERSIBILITY_REVERSIBLE,
            default_confirm=False,
        )

    return RiskSpec(
        risk_class=POLICY_RISK_READ,
        side_effects=POLICY_SIDE_EFFECT_NONE,
        reversibility=POLICY_REVERSIBILITY_UNKNOWN,
        default_confirm=False,
    )
