from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .constants import (
    A2A_POLICY_ACTION_ALLOW,
    A2A_POLICY_ACTION_DENY,
    A2A_POLICY_ACTIONS,
)
from .models import Envelope


@dataclass
class PolicyRule:
    action: str
    from_agent: Optional[str] = None
    to_agent: Optional[str] = None
    to_capability: Optional[str] = None
    method_prefix: Optional[str] = None
    message_type: Optional[str] = None

    def matches(self, envelope: Envelope, resolved_agent: str) -> bool:
        return (
            (not self.from_agent or self.from_agent == envelope.from_agent)
            and (not self.to_agent or self.to_agent == resolved_agent)
            and (
                not self.to_capability
                or self.to_capability == (envelope.to_capability or "")
            )
            and (
                not self.method_prefix or envelope.method.startswith(self.method_prefix)
            )
            and (not self.message_type or self.message_type == envelope.type)
        )


class PolicyEngine:
    def __init__(
        self,
        *,
        default_action: str = A2A_POLICY_ACTION_ALLOW,
        rules: list[PolicyRule] | None = None,
    ) -> None:
        self.default_action = (
            A2A_POLICY_ACTION_DENY
            if default_action.lower() == A2A_POLICY_ACTION_DENY
            else A2A_POLICY_ACTION_ALLOW
        )
        self.rules = list(rules or [])

    @classmethod
    def from_config(cls, default_action: str, raw_rules: list[dict]) -> "PolicyEngine":
        rules: list[PolicyRule] = []
        for raw in raw_rules:
            if not isinstance(raw, dict):
                continue
            action = _normalized_action(raw.get("action", A2A_POLICY_ACTION_DENY))
            rules.append(
                PolicyRule(
                    action=action,
                    from_agent=_norm(raw.get("from_agent")),
                    to_agent=_norm(raw.get("to_agent")),
                    to_capability=_norm(raw.get("to_capability")),
                    method_prefix=_norm(raw.get("method_prefix")),
                    message_type=_norm(raw.get("message_type")),
                )
            )
        return cls(default_action=default_action, rules=rules)

    def is_allowed(self, envelope: Envelope, resolved_agent: str) -> bool:
        decision = self.default_action
        for rule in self.rules:
            if rule.matches(envelope, resolved_agent):
                decision = rule.action
        return decision == A2A_POLICY_ACTION_ALLOW


def _norm(value: object) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalized_action(value: object) -> str:
    action = str(value).lower()
    return action if action in A2A_POLICY_ACTIONS else A2A_POLICY_ACTION_DENY
