from dataclasses import dataclass
from typing import Any, Callable

from openminion.modules.tool.plugin_api import PolicyAdapter, PolicyDecision
from openminion.modules.tool.registry.catalog import ToolSpec
from openminion.modules.tool.contracts.model_ids import MODEL_EXEC_RUN
from openminion.tools.exec.command_parser import is_read_only_exec_command
from openminion.tools.exec.hints import read_only_discovery_hint_for_command
from openminion.tools.exec.process import resolve_shell_family

from ..runtime.security import (
    DECISION_ALLOW,
    DECISION_REQUIRE_APPROVAL,
    SecurityPolicyAction,
    SecurityPolicyCheck,
    SecurityPolicyContext,
    SecurityPolicyDecision,
    SecurityPolicyEngine,
    SecurityPolicyActor,
    ToolBudgetState,
)
from .blast_radius import CompositionBoundaryAdapter

ToolPolicyLookup = Callable[[str], Any]

_EXEC_RUN_TOOL_NAMES = frozenset({MODEL_EXEC_RUN, "runtime.exec.run"})


@dataclass
class ExecutionBoundaryPolicyAdapter(PolicyAdapter):
    policy: SecurityPolicyEngine
    actor: SecurityPolicyActor
    context: SecurityPolicyContext
    tool_policy_lookup: ToolPolicyLookup | None = None
    budget_state: ToolBudgetState | None = None
    blast_radius_adapter: CompositionBoundaryAdapter | None = None

    def _tool_profile(
        self, tool_name: str, tool_spec: ToolSpec
    ) -> tuple[str, int, tuple[str, ...]]:
        risk = "high" if getattr(tool_spec, "dangerous", False) else "medium"
        budget_cost = 1
        required_scopes: tuple[str, ...] = ()

        if self.tool_policy_lookup is not None:
            try:
                profile = self.tool_policy_lookup(tool_name)
            except Exception:
                profile = None
            if profile is not None:
                required_scopes = tuple(
                    getattr(profile, "required_scopes_all", ()) or ()
                )
                risk = (
                    str(getattr(profile, "risk", risk) or risk).strip().lower() or risk
                )
                try:
                    budget_cost = max(
                        1, int(getattr(profile, "budget_cost", budget_cost))
                    )
                except (TypeError, ValueError):
                    budget_cost = 1

        return risk, budget_cost, required_scopes

    def _decision_to_policy_decision(
        self,
        tool_name: str,
        decision: SecurityPolicyDecision,
    ) -> PolicyDecision:
        requires_confirm = decision.decision == DECISION_REQUIRE_APPROVAL
        allowed = decision.decision == DECISION_ALLOW
        code = (
            "require_approval"
            if requires_confirm
            else str(decision.reason_code or "policy_denied")
        )
        return PolicyDecision(
            allowed=allowed,
            reason=str(decision.reason_code or "policy_denied"),
            code=code,
            requires_confirm=requires_confirm,
            details={
                "policy_version": decision.policy_version,
                "decision": decision.decision,
                "tool_name": tool_name,
                **(decision.details or {}),
            },
        )

    def evaluate(
        self, *, tool_name: str, tool_spec: ToolSpec, args: dict[str, Any]
    ) -> PolicyDecision:
        if self.blast_radius_adapter is not None:
            self.blast_radius_adapter.step(tool_spec)
        discovery_hint = _read_only_exec_denial_hint(tool_name=tool_name, args=args)
        if discovery_hint is not None:
            hint_tool, hint_fix = discovery_hint
            return PolicyDecision(
                allowed=False,
                reason="read_only_exec_has_structured_tool",
                code="POLICY_DENIED",
                requires_confirm=False,
                details={
                    "decision": "deny",
                    "tool_name": tool_name,
                    "suggested_tool": hint_tool,
                    "suggested_fix": hint_fix,
                },
            )
        if _read_only_exec_allowed(tool_name=tool_name, args=args):
            return PolicyDecision(
                allowed=True,
                reason="read_only_exec_allowed",
                code="OK",
                requires_confirm=False,
                modified_args=dict(args),
                details={
                    "decision": "allow",
                    "tool_name": tool_name,
                    "action_class": "read_only_discovery",
                },
            )
        risk, budget_cost, required_scopes = self._tool_profile(tool_name, tool_spec)
        decision = self.policy.evaluate(
            SecurityPolicyCheck(
                actor=self.actor,
                action=SecurityPolicyAction(
                    resource="tool",
                    verb="execute",
                    risk=risk,
                    tool_name=tool_name,
                    required_scopes_all=frozenset(required_scopes),
                ),
                context=self.context,
            )
        )
        policy_decision = self._decision_to_policy_decision(tool_name, decision)
        if not policy_decision.allowed:
            return policy_decision

        if self.budget_state is not None:
            budget_decision = self.policy.evaluate_tool_budget(
                tool_name=tool_name,
                budget_cost=budget_cost,
                state=self.budget_state,
            )
            budget_policy_decision = self._decision_to_policy_decision(
                tool_name, budget_decision
            )
            if not budget_policy_decision.allowed:
                return budget_policy_decision
            self.policy.record_tool_budget_usage(
                tool_name=tool_name,
                budget_cost=budget_cost,
                state=self.budget_state,
            )

        return PolicyDecision(
            allowed=True,
            reason="policy passed",
            code="OK",
            modified_args=dict(args),
            details={"tool_name": tool_name},
        )


def _read_only_exec_denial_hint(
    *, tool_name: str, args: dict[str, Any]
) -> tuple[str, str] | None:
    if str(tool_name or "").strip() not in _EXEC_RUN_TOOL_NAMES:
        return None
    command = str(args.get("command", "") or "").strip()
    if not command:
        return None
    return read_only_discovery_hint_for_command(command)


def _read_only_exec_allowed(*, tool_name: str, args: dict[str, Any]) -> bool:
    if str(tool_name or "").strip() not in _EXEC_RUN_TOOL_NAMES:
        return False
    command = str(args.get("command", "") or "").strip()
    if not command:
        return False
    return is_read_only_exec_command(command, shell_family=resolve_shell_family())


def build_execution_boundary_policy_adapter(
    *,
    policy: SecurityPolicyEngine,
    actor: SecurityPolicyActor,
    context: SecurityPolicyContext,
    tool_policy_lookup: ToolPolicyLookup | None = None,
    budget_state: ToolBudgetState | None = None,
    blast_radius_adapter: CompositionBoundaryAdapter | None = None,
) -> ExecutionBoundaryPolicyAdapter:
    return ExecutionBoundaryPolicyAdapter(
        policy=policy,
        actor=actor,
        context=context,
        tool_policy_lookup=tool_policy_lookup,
        budget_state=budget_state,
        blast_radius_adapter=blast_radius_adapter,
    )
