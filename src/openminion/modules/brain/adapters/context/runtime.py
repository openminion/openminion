from typing import Any, cast

from openminion.modules.brain.interfaces import (
    BRAIN_ADAPTER_INTERFACE_VERSION,
    ContextAPI,
    SessionArtifactAPI,
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


def _turn_segment_ids(message: dict[str, Any]) -> list[str]:
    meta = message.get("meta")
    if not isinstance(meta, dict):
        return []
    return [
        normalized[5:]
        for item in meta.get("segment_ids", [])
        if (normalized := str(item or "").strip()).startswith("turn:")
        and len(normalized) > 5
    ]


def _normalized_role(value: Any) -> str:
    role = str(value or "").strip().lower()
    if role in {"agent", "outbound"}:
        return "assistant"
    if role == "inbound":
        return "user"
    return role


def _selected_pack_turns(*, payload: dict[str, Any], turns: list[Any]) -> list[Any]:
    messages = [
        message for message in payload.get("messages", []) if isinstance(message, dict)
    ]
    selected: dict[int, dict[str, Any]] = {}
    unused = {index for index, turn in enumerate(turns) if isinstance(turn, dict)}
    upper_bound = len(turns)
    for message in reversed(messages):
        segment_ids = _turn_segment_ids(message)
        exact_index = next(
            (
                index
                for index in range(upper_bound - 1, -1, -1)
                if index in unused
                and str(turns[index].get("turn_id") or "").strip() in segment_ids
            ),
            None,
        )
        matched_index = exact_index
        alias = None
        if matched_index is None and len(segment_ids) == 1:
            message_role = _normalized_role(message.get("role"))
            message_content = str(message.get("content") or "").strip()
            matched_index = next(
                (
                    index
                    for index in range(upper_bound - 1, -1, -1)
                    if index in unused
                    and _normalized_role(turns[index].get("role")) == message_role
                    and str(turns[index].get("content") or "").strip()
                    == message_content
                ),
                None,
            )
            alias = segment_ids[0] if matched_index is not None else None
        if matched_index is None:
            continue
        copied = dict(turns[matched_index])
        if alias is not None:
            copied["context_segment_id"] = alias
        selected[matched_index] = copied
        unused.remove(matched_index)
        upper_bound = matched_index

    for index in range(len(turns) - 1, -1, -1):
        turn = turns[index]
        if not isinstance(turn, dict):
            continue
        if _normalized_role(turn.get("role")) == "user":
            selected.setdefault(index, dict(turn))
            break
    return [selected[index] for index in sorted(selected)]


def _without_detached_artifacts(turns: list[Any], detached_refs: set[str]) -> list[Any]:
    filtered: list[Any] = []
    for turn in turns:
        if not isinstance(turn, dict):
            continue
        copied = dict(turn)
        attachments = copied.get("attachments")
        if isinstance(attachments, list):
            copied["attachments"] = [
                ref for ref in attachments if str(ref) not in detached_refs
            ]
        filtered.append(copied)
    return filtered


class ContextCtlAdapter(ContextAPI):
    contract_version = BRAIN_ADAPTER_INTERFACE_VERSION

    def __init__(
        self,
        service: Any,
        *,
        session_store: Any | None = None,
        runtime_token_budget: int | None = None,
        owned_identity_client: Any | None = None,
        owned_memory_client: Any | None = None,
        owned_artifact_client: Any | None = None,
        owned_skill_client: Any | None = None,
    ) -> None:
        self.service = service
        self._session_store = session_store
        self._runtime_token_budget = runtime_token_budget
        self._owned_identity_client = owned_identity_client
        self._owned_memory_client = owned_memory_client
        self._owned_artifact_client = owned_artifact_client
        self._owned_skill_client = owned_skill_client
        self._closed = False

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
        budgets_override = default_budgets_for(purpose_name)

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
            self_awareness=_dict_hint(hints, "self_awareness"),
        )
        pack = self.service.build_pack(req)
        result = cast(dict[str, Any], pack.model_dump())
        if self._session_store is not None:
            selected_turns = _selected_pack_turns(
                payload=result,
                turns=list(self._session_store.list_turns(session_id)),
            )
            if isinstance(self._session_store, SessionArtifactAPI):
                selected_turns = _without_detached_artifacts(
                    selected_turns,
                    set(self._session_store.get_detached_artifact_refs(session_id)),
                )
            result["turns"] = selected_turns
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

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.service.close()
        if self._owned_identity_client is not None:
            self._owned_identity_client.close()
            self._owned_identity_client = None
        if self._owned_memory_client is not None:
            self._owned_memory_client.close()
            self._owned_memory_client = None
        if self._owned_artifact_client is not None:
            self._owned_artifact_client.close()
            self._owned_artifact_client = None
        if self._owned_skill_client is not None:
            self._owned_skill_client.close()
            self._owned_skill_client = None
