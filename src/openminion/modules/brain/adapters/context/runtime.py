from typing import Any, cast

from openminion.modules.brain.interfaces import (
    ContextAPI,
    BRAIN_ADAPTER_INTERFACE_VERSION,
)
from openminion.modules.context.pack.semantics import (
    resolve_context_total_token_budget,
)
from openminion.modules.tool.schema_service import ToolSchemaService


_TOOL_SCHEMA_SERVICE = ToolSchemaService()
_PHASE_PROMPT_HINT_KEYS = {
    "closure_candidate_reason",
    "closure_action_summary",
    "closure_action_outputs",
    "closure_sub_intents",
    "closure_intent_outcomes",
    "closure_success_criteria",
    "plan_sub_intents",
    "completed_intent_states",
    "remaining_intent_states",
    "blocked_intent_states",
    "adaptive_revision_context",
    "feasibility_sub_intents",
    "feasibility_plan_steps",
    "feasibility_runtime_facts",
}


def _dict_hint(hints: dict[str, Any], key: str) -> dict[str, Any]:
    value = hints.get(key)
    return dict(value) if isinstance(value, dict) else {}


class ContextCtlAdapter(ContextAPI):
    contract_version = BRAIN_ADAPTER_INTERFACE_VERSION

    def __init__(
        self, service: Any, *, runtime_token_budget: int | None = None
    ) -> None:
        self.service = service
        self._runtime_token_budget = runtime_token_budget

    def build(
        self,
        *,
        session_id: str,
        agent_id: str,
        purpose: str,
        budget: dict[str, Any],
        hints: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from openminion.modules.context.schemas import (
            BuildPackRequest,
            BuildConstraints,
            Purpose,
            default_budgets_for,
        )

        hints = hints or {}
        user_query = str(hints.get("query") or hints.get("user_input") or "").strip()
        phase_hints = {
            key: value for key, value in hints.items() if key in _PHASE_PROMPT_HINT_KEYS
        }
        live_state_overlay = _dict_hint(hints, "live_state_overlay")
        budget_telemetry = _dict_hint(hints, "budget_telemetry")
        runtime_tool_schemas = [
            item
            for item in (hints.get("runtime_tool_schemas") or [])
            if isinstance(item, dict)
        ]
        prompt_tools_enabled = _TOOL_SCHEMA_SERVICE.prompt_schemas_enabled(
            explicit=hints.get("prompt_tool_schemas_enabled"),
            default=False,
        )
        bundle = _TOOL_SCHEMA_SERVICE.get_tools_for_purpose(
            purpose=purpose,
            query=user_query,
            caller_context="context_build",
            execution_tools=runtime_tool_schemas,
            structured_schema=None,
            prompt_schemas_enabled=prompt_tools_enabled,
        )
        if bundle.execution_tools:
            hints["runtime_tool_schemas"] = [
                dict(item) for item in bundle.execution_tools
            ]
        else:
            hints.pop("runtime_tool_schemas", None)
        if bundle.prompt_tool_stubs:
            hints["tool_schemas"] = [dict(item) for item in bundle.prompt_tool_stubs]
            hints["tool_aware"] = True
        else:
            hints.pop("tool_schemas", None)
            hints.pop("tool_aware", None)
        llm_call_id = (
            str(hints.get("_llm_call_id") or hints.get("llm_call_id") or "").strip()
            or None
        )
        mode_name = str(hints.get("mode_name") or "").strip().lower() or None
        purpose_name = cast(Purpose, purpose)
        try:
            budgets_override = default_budgets_for(purpose_name)
        except Exception:
            budgets_override = default_budgets_for("plan")  # fallback

        request_token_budget = budget.get("max_tokens") or budget.get("identity_tokens")
        budgets_override.total_max_tokens = resolve_context_total_token_budget(
            purpose=purpose,
            runtime_token_budget=self._runtime_token_budget,
            requested_token_budget=request_token_budget,
        )

        gateway_system_context = str(hints.get("gateway_system_context") or "").strip()
        req = BuildPackRequest(
            session_id=session_id,
            agent_id=agent_id,
            purpose=purpose_name,
            mode_name=mode_name,
            query=user_query,
            constraints=BuildConstraints.model_validate(hints) if hints else None,
            budgets_override=budgets_override,
            llm_call_id=llm_call_id,
            introspection_intent=bool(hints.get("introspection_intent", False)),
            budget_telemetry=budget_telemetry,
            live_state_overlay=live_state_overlay,
            phase_hints=phase_hints,
            gateway_system_context=gateway_system_context,
        )
        pack = self.service.build_pack(req)
        result = cast(dict[str, Any], pack.model_dump())
        if hints:
            result["hints"] = hints
        return result

    def make_delta(
        self,
        *,
        session_id: str,
        agent_id: str,
        content: str = "",
    ) -> str:
        delta = self.service.make_delta(
            session_id=session_id, agent_id=agent_id, content=content
        )
        return str(delta.delta_ref)

    def maybe_compact(
        self,
        *,
        session_id: str,
        agent_id: str,
    ) -> bool:
        return bool(self.service.maybe_compact(session_id=session_id))
