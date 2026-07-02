from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..errors import ToolRuntimeError
from ..plugin_api import (
    PolicyAdapter,
    PolicyDecision,
    SafetyAdapter,
    SafetyDecision,
)
from ..runtime.policy import Policy
from ..runtime.policy_checks import run_policy_preflight
from ..registry.catalog import ToolSpec
from ..contracts.schemas import Scope


@dataclass
class AllowAllSafetyAdapter(SafetyAdapter):
    reason: str = "Safety checks passed"

    def evaluate(self, *, tool: str, args: dict[str, Any]) -> SafetyDecision:
        return SafetyDecision(allowed=True, reason=self.reason)


@dataclass
class LocalPolicyAdapter(PolicyAdapter):
    policy: Policy
    workspace: Path
    scope: Scope
    confirm: bool

    def evaluate(
        self, *, tool_name: str, tool_spec: ToolSpec, args: dict[str, Any]
    ) -> PolicyDecision:
        try:
            run_policy_preflight(
                policy=self.policy,
                tool_spec=tool_spec,
                tool_name=tool_name,
                args=args,
                effective_scope=self.scope,
                confirm=self.confirm,
                workspace=self.workspace,
            )
        except ToolRuntimeError as exc:  # pragma: no cover - exercised in higher-level tests
            requires_confirm = str(exc.code or "").upper() == "CONFIRM_REQUIRED"
            return PolicyDecision(
                allowed=False,
                reason=exc.message,
                code=exc.code,
                requires_confirm=requires_confirm,
                details=exc.details,
            )
        return PolicyDecision(
            allowed=True, reason="Policy checks passed", modified_args=dict(args)
        )
