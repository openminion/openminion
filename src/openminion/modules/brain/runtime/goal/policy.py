"""Goal-action authorization helpers for runtime policy checks."""

from dataclasses import dataclass
from typing import Any, Literal

from ..escalation import ActionRiskTier, goal_policy_risk_tier


GoalExecutionPolicy = Literal["suggest", "auto_safe", "auto_full"]
GoalActionType = Literal["watch", "task", "suggest", "none"]


@dataclass(frozen=True)
class GoalAuthorization:
    """Typed result returned by `authorize_goal_action`."""

    allowed: bool
    requires_user_confirm: bool
    reason: str
    risk_tier: ActionRiskTier


_REASON_AUTO_FULL = "policy_auto_full"
_REASON_AUTO_SAFE_WATCH_TASK = "policy_auto_safe_watch_task"
_REASON_AUTO_SAFE_SUGGEST_ONLY = "policy_auto_safe_non_watch_task"
_REASON_SUGGEST_DEFAULT = "policy_suggest"
_REASON_ACTION_NONE = "action_type_none"
_REASON_UNKNOWN_POLICY = "policy_unknown_default_safe"


def authorize_goal_action(
    *,
    profile_policy: str | None,
    action_type: str | None,
) -> GoalAuthorization:
    """Resolve `(profile_policy, action_type)` into `GoalAuthorization`."""
    normalized_action = str(action_type or "").strip().lower()
    if normalized_action == "none":
        return GoalAuthorization(
            allowed=False,
            requires_user_confirm=False,
            reason=_REASON_ACTION_NONE,
            risk_tier=goal_policy_risk_tier(
                allowed=False,
                requires_user_confirm=False,
            ),
        )

    normalized_policy = str(profile_policy or "").strip().lower() or "suggest"

    if normalized_policy == "auto_full":
        return GoalAuthorization(
            allowed=True,
            requires_user_confirm=False,
            reason=_REASON_AUTO_FULL,
            risk_tier=goal_policy_risk_tier(
                allowed=True,
                requires_user_confirm=False,
            ),
        )

    if normalized_policy == "auto_safe":
        if normalized_action in {"watch", "task"}:
            return GoalAuthorization(
                allowed=True,
                requires_user_confirm=False,
                reason=_REASON_AUTO_SAFE_WATCH_TASK,
                risk_tier=goal_policy_risk_tier(
                    allowed=True,
                    requires_user_confirm=False,
                ),
            )
        return GoalAuthorization(
            allowed=False,
            requires_user_confirm=True,
            reason=_REASON_AUTO_SAFE_SUGGEST_ONLY,
            risk_tier=goal_policy_risk_tier(
                allowed=False,
                requires_user_confirm=True,
            ),
        )

    if normalized_policy == "suggest":
        return GoalAuthorization(
            allowed=False,
            requires_user_confirm=True,
            reason=_REASON_SUGGEST_DEFAULT,
            risk_tier=goal_policy_risk_tier(
                allowed=False,
                requires_user_confirm=True,
            ),
        )

    return GoalAuthorization(
        allowed=False,
        requires_user_confirm=True,
        reason=_REASON_UNKNOWN_POLICY,
        risk_tier=goal_policy_risk_tier(
            allowed=False,
            requires_user_confirm=True,
        ),
    )


def render_goal_execution_policy(profile: Any) -> str:
    """Render the policy line for the model profile."""
    if profile is None:
        return ""
    policy = getattr(profile, "goal_execution_policy", None)
    if not policy:
        return ""
    normalized = str(policy).strip().lower()
    if normalized == "auto_full":
        body = (
            "auto_full: you may auto-create any action backing a recalled "
            "goal (write actions still gated by WBW)"
        )
    elif normalized == "auto_safe":
        body = (
            "auto_safe: you may auto-create read-only watches and tasks "
            "from recalled goals; ask the user for any other action"
        )
    elif normalized == "suggest":
        body = (
            "suggest: surface recalled goals as suggestions; ask the user "
            "before creating any backing watch / task / action"
        )
    else:
        body = f"unknown ({normalized}); treat as suggest (safe default)"
    return f"goal_execution_policy: {body}"


__all__ = [
    "GoalActionType",
    "GoalAuthorization",
    "GoalExecutionPolicy",
    "authorize_goal_action",
    "render_goal_execution_policy",
]
