from __future__ import annotations

from .schemas import PackPolicyProfile, PolicyDecision, PolicyVerb


def resolve_policy(
    profile: PackPolicyProfile,
    *,
    verb: PolicyVerb,
    capability_scope: str,
) -> PolicyDecision:
    exact = next(
        (
            rule
            for rule in profile.rules
            if rule.verb == verb and rule.capability_scope == capability_scope
        ),
        None,
    )
    if exact is not None:
        return exact.decision
    fallback = next(
        (
            rule
            for rule in profile.rules
            if rule.verb == verb and rule.capability_scope == "*"
        ),
        None,
    )
    return fallback.decision if fallback is not None else profile.default_decision
