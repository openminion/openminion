from __future__ import annotations

import json
from typing import Any, NamedTuple

from pydantic import ValidationError

from openminion.modules.brain.execution.public_taxonomy import (
    public_mode_name_for_mode_name,
)
from openminion.modules.brain.schemas import DelegationContext
from openminion.modules.llm.constants import REQUESTABLE_TOOL_NAMES_METADATA_KEY
from openminion.modules.llm.schemas import Message, ToolSpec

from ..budget_control import (
    _emit_budget_event,
    _emit_high_watermark_if_needed,
    _general_profile_name,
)
from ..budget import _effective_cap
from ..contracts import (
    AdaptiveToolLoopContext,
    AdaptiveToolLoopProfile,
    AdaptiveToolLoopState,
    resolve_allowed_tools,
)
from ..plan_control import (
    PLAN_TOOL_NAME,
    plan_tool_enabled,
    with_enabled_plan_tool_spec,
)
from ..response_payloads import (
    _CONFIDENT_COMPLETE_GUIDANCE,
    _DELEGATION_RESULT_SUMMARY_GUIDANCE,
    _FINALIZATION_STATUS_GUIDANCE,
    _GOAL_DECLARATION_GUIDANCE,
    _GOAL_REVISION_GUIDANCE,
    _MEMORY_CONSOLIDATION_GUIDANCE,
    _META_RULE_PREFERENCE_GUIDANCE,
    _PENDING_TURN_CONTEXT_GUIDANCE,
    _SESSION_WORK_SUMMARY_GUIDANCE,
    _TASK_PLAN_GUIDANCE,
    _TASK_PLAN_PROGRESS_GUIDANCE,
    _WATCH_ACTION_GUIDANCE,
    _WATCH_OUTCOME_GUIDANCE,
)
from ..shortlisting import (
    TOOL_REQUEST_TOOL_NAME,
    build_inactive_tool_directory_message,
    with_tool_request_spec,
)
from ..startup import initialize_loop_runtime_state
from ..telemetry import _current_turn_scope_id, _public_loop_tag


def _enabled_module_state_payload(
    loop_ctx: AdaptiveToolLoopContext,
    *,
    key: str,
) -> dict[str, Any] | None:
    raw = loop_ctx.state.module_state.get(key)
    if not raw or not bool(raw.get("enabled", False)):
        return None
    return dict(raw)


def _has_system_message(messages: list[Message], content: str) -> bool:
    return any(
        message.role == "system" and message.content.strip() == content
        for message in messages
    )


def _loop_request_metadata(
    profile: AdaptiveToolLoopProfile,
    requestable_specs: list[ToolSpec],
) -> dict[str, Any] | None:
    metadata_override = profile.llm_request_overrides.get("metadata")
    metadata = (
        dict(metadata_override or {}) if isinstance(metadata_override, dict) else None
    )
    if requestable_specs:
        metadata = dict(metadata or {})
        metadata[REQUESTABLE_TOOL_NAMES_METADATA_KEY] = json.dumps(
            [spec.name for spec in requestable_specs if spec.name.strip()]
        )
    return metadata


def _ensure_system_message(
    messages: list[Message], *, index: int, content: str
) -> bool:
    if _has_system_message(messages, content):
        return False
    messages.insert(index, Message(role="system", content=content))
    return True


def _tool_efficiency_guidance(profile: AdaptiveToolLoopProfile) -> str:
    max_tool_calls = profile.max_tool_calls_per_loop
    tool_call_budget = (
        str(int(max_tool_calls)) if max_tool_calls is not None else "the available"
    )
    return "\n".join(
        [
            "Tool efficiency rules:",
            "1. You have a limited tool budget per turn. Prefer approaches that use fewer tool calls to reach a final answer.",
            "2. When search results include snippets, summarize from the snippets directly instead of fetching each URL individually.",
            "3. Only fetch a URL if the search snippet is insufficient and you need the full article content for the user's specific request.",
            "4. For current-events, latest-news, or top-N requests, one or two searches are usually enough; pick the top items from result titles/snippets and answer.",
            "5. These efficiency rules override any skill or example procedure that suggests a fixed number of searches; stop searching once you have enough evidence to answer.",
            "6. Batch related lookups when possible instead of making them one at a time.",
            "7. Follow any operation order the user specifies; do not perform a later operation before an earlier one.",
            "8. Stop after completing the requested operations; do not add related calls the user did not request.",
            "9. If a tool reports a budget or per-tool limit error, do not call another tool; produce the best final answer from the results already available.",
            "10. Always produce a final answer before your tool budget runs out; a partial sourced answer is better than no answer.",
            f"11. Your current budget is approximately {int(profile.max_iterations)} iterations / {tool_call_budget} tool calls.",
        ]
    )


def _memory_consolidation_context(
    loop_ctx: AdaptiveToolLoopContext,
) -> dict[str, Any] | None:
    return _enabled_module_state_payload(loop_ctx, key="memory_consolidation")


def _delegated_child_context(
    loop_ctx: AdaptiveToolLoopContext,
) -> dict[str, Any] | None:
    return _enabled_module_state_payload(loop_ctx, key="delegation")


def _delegated_child_context_message(payload: dict[str, Any]) -> Message | None:
    raw_context = payload.get("parent_context")
    if not isinstance(raw_context, dict):
        return None
    try:
        context = DelegationContext.model_validate(raw_context)
    except ValidationError:
        return None
    lines = ["[PARENT CONTEXT]"]
    if context.intent_id:
        lines.append(f"intent_id: {context.intent_id}")
    if context.summary:
        lines.append(f"summary: {context.summary}")
    if context.artifacts:
        lines.append("artifacts: " + ", ".join(context.artifacts))
    return Message(role="system", content="\n".join(lines))


def _memory_consolidation_context_message(payload: dict[str, Any]) -> Message | None:
    candidates = [
        item
        for item in list(payload.get("candidates", []) or [])
        if isinstance(item, dict)
    ]
    if not candidates:
        return Message(
            role="system",
            content=(
                "[MEMORY CONSOLIDATION CANDIDATES]\n"
                "No pending candidates were provided for this consolidation turn. "
                "Reply with a short final summary stating that there was nothing to consolidate."
            ),
        )
    lines = ["[MEMORY CONSOLIDATION CANDIDATES]"]
    for index, item in enumerate(candidates, start=1):
        lines.append(
            (
                f"{index}. id={str(item.get('candidate_id', '')).strip()} "
                f"type={str(item.get('record_type', '')).strip()} "
                f"confidence={float(item.get('confidence', 0.0) or 0.0):.2f} "
                f"source_session={str(item.get('source_session', '')).strip()}"
            ).strip()
        )
        title = str(item.get("title", "") or "").strip()
        if title:
            lines.append(f"   title: {title}")
        preview = str(item.get("content_preview", "") or "").strip()
        if preview:
            lines.append(f"   preview: {preview}")
    return Message(role="system", content="\n".join(lines))


class LoopFrameSetup(NamedTuple):
    """Outputs of `prepare_loop_frame` consumed by the per-iteration loop body."""

    public_mode_name: str
    public_mode_tag: str
    tool_request_enabled: bool
    requestable_specs: list[ToolSpec]
    requestable_specs_by_name: dict[str, ToolSpec]
    active_tool_specs: list[ToolSpec]
    active_tool_names: set[str]
    allowed_tools: frozenset[str]
    seeded_queue: list[Any]
    loop_state: AdaptiveToolLoopState
    max_output_tokens: Any
    metadata: dict[str, Any] | None
    turn_scope_id: Any
    runtime_state: Any
    pending_response: Any


def prepare_loop_frame(
    loop_ctx: AdaptiveToolLoopContext,
    *,
    profile: AdaptiveToolLoopProfile,
    model: str,
    initial_messages: list[Message],
    tool_specs: list[ToolSpec],
    requestable_tool_specs: list[ToolSpec] | tuple[ToolSpec, ...] | None,
    initial_state: AdaptiveToolLoopState | None,
    seed_response: Any,
    seeded_commands: list[Any] | tuple[Any, ...] | None,
) -> LoopFrameSetup:
    """Initial frame setup for `run_adaptive_tool_loop`."""
    public_mode_name = (
        public_mode_name_for_mode_name(profile.mode_name) or profile.mode_name
    )
    public_mode_tag = _public_loop_tag(profile.mode_name)
    requestable_specs = list(requestable_tool_specs or [])
    tool_request_enabled = bool(requestable_specs)
    requestable_specs_by_name = {
        spec.name.strip(): spec for spec in requestable_specs if spec.name.strip()
    }
    active_tool_specs = (
        with_tool_request_spec(tool_specs) if tool_request_enabled else list(tool_specs)
    )
    active_tool_specs = with_enabled_plan_tool_spec(profile, active_tool_specs)
    active_tool_names = {
        spec.name.strip()
        for spec in active_tool_specs
        if spec.name.strip() not in {TOOL_REQUEST_TOOL_NAME, PLAN_TOOL_NAME}
    }
    allowed_tools = resolve_allowed_tools(
        profile=profile,
        runtime_tool_names=[spec.name.strip() for spec in active_tool_specs],
    )
    if tool_request_enabled:
        allowed_tools = frozenset({*allowed_tools, TOOL_REQUEST_TOOL_NAME})
    if plan_tool_enabled(profile):
        allowed_tools = frozenset({*allowed_tools, PLAN_TOOL_NAME})
    seeded_queue = list(seeded_commands or [])
    if not active_tool_specs and allowed_tools and not seeded_queue:
        raise ValueError(
            "tool_specs are required for the resolved adaptive tool surface"
        )

    loop_state = initial_state or AdaptiveToolLoopState(messages=list(initial_messages))
    if not loop_state.messages:
        loop_state.messages = list(initial_messages)
    if not _has_system_message(loop_state.messages, _CONFIDENT_COMPLETE_GUIDANCE):
        loop_state.messages.insert(
            0,
            Message(role="system", content=_CONFIDENT_COMPLETE_GUIDANCE),
        )
    if not _has_system_message(loop_state.messages, _FINALIZATION_STATUS_GUIDANCE):
        loop_state.messages.insert(
            1,
            Message(role="system", content=_FINALIZATION_STATUS_GUIDANCE),
        )
    _ensure_system_message(
        loop_state.messages,
        index=2,
        content=_PENDING_TURN_CONTEXT_GUIDANCE,
    )
    consolidation_payload = _memory_consolidation_context(loop_ctx)
    if consolidation_payload is not None and _ensure_system_message(
        loop_state.messages,
        index=1,
        content=_MEMORY_CONSOLIDATION_GUIDANCE,
    ):
        consolidation_message = _memory_consolidation_context_message(
            consolidation_payload
        )
        if consolidation_message is not None:
            loop_state.messages.insert(2, consolidation_message)
    _ensure_system_message(
        loop_state.messages,
        index=1,
        content=_SESSION_WORK_SUMMARY_GUIDANCE,
    )
    _ensure_system_message(
        loop_state.messages,
        index=1,
        content=_TASK_PLAN_GUIDANCE,
    )
    _ensure_system_message(
        loop_state.messages,
        index=1,
        content=_TASK_PLAN_PROGRESS_GUIDANCE,
    )
    delegated_child_payload = _delegated_child_context(loop_ctx)
    if delegated_child_payload is not None and _ensure_system_message(
        loop_state.messages,
        index=2,
        content=_DELEGATION_RESULT_SUMMARY_GUIDANCE,
    ):
        parent_context_message = _delegated_child_context_message(
            delegated_child_payload
        )
        if parent_context_message is not None:
            loop_state.messages.insert(3, parent_context_message)
    _ensure_system_message(
        loop_state.messages,
        index=2,
        content=_META_RULE_PREFERENCE_GUIDANCE,
    )
    _ensure_system_message(
        loop_state.messages,
        index=2,
        content=_GOAL_DECLARATION_GUIDANCE,
    )
    _ensure_system_message(
        loop_state.messages,
        index=3,
        content=_GOAL_REVISION_GUIDANCE,
    )
    from openminion.modules.brain.runtime.goal.policy import (
        render_goal_execution_policy,
    )

    policy_line = render_goal_execution_policy(profile)
    if policy_line:
        _ensure_system_message(loop_state.messages, index=3, content=policy_line)
    if _general_profile_name(profile):
        tool_efficiency_guidance = _tool_efficiency_guidance(profile)
        _ensure_system_message(
            loop_state.messages,
            index=2,
            content=tool_efficiency_guidance,
        )
    if profile.profile_name == "watch_check_v1":
        _ensure_system_message(
            loop_state.messages,
            index=2,
            content=_WATCH_OUTCOME_GUIDANCE,
        )
    if profile.profile_name == "watch_action_v1":
        _ensure_system_message(
            loop_state.messages,
            index=2,
            content=_WATCH_ACTION_GUIDANCE,
        )
    if tool_request_enabled and not any(
        message.role == "system"
        and message.meta.get("tool_schema_shortlisting") == "inactive_directory"
        for message in loop_state.messages
    ):
        inactive_directory_message = build_inactive_tool_directory_message(
            requestable_tool_specs=requestable_specs,
            active_tool_names=active_tool_names,
        )
        if inactive_directory_message is not None:
            loop_state.messages.append(inactive_directory_message)

    max_output_tokens = profile.llm_request_overrides.get("max_output_tokens")
    metadata = _loop_request_metadata(profile, requestable_specs)

    turn_scope_id = _current_turn_scope_id(loop_ctx)

    runtime_state = initialize_loop_runtime_state(
        loop_ctx=loop_ctx,
        loop_state=loop_state,
        profile=profile,
        model=model,
        turn_scope_id=turn_scope_id,
    )
    if profile.adaptive_budget_config is not None:
        _emit_budget_event(
            loop_ctx,
            "budget.allocated",
            {
                "soft_cap": int(profile.max_iterations),
                "effective_max_iterations": _effective_cap(profile, loop_state),
                "hard_cap": 128,
                "source": "adaptive_budget",
            },
        )
        _emit_high_watermark_if_needed(
            loop_ctx=loop_ctx,
            loop_state=loop_state,
            cap=_effective_cap(profile, loop_state),
        )

    return LoopFrameSetup(
        public_mode_name=public_mode_name,
        public_mode_tag=public_mode_tag,
        tool_request_enabled=tool_request_enabled,
        requestable_specs=requestable_specs,
        requestable_specs_by_name=requestable_specs_by_name,
        active_tool_specs=active_tool_specs,
        active_tool_names=active_tool_names,
        allowed_tools=allowed_tools,
        seeded_queue=seeded_queue,
        loop_state=loop_state,
        max_output_tokens=max_output_tokens,
        metadata=metadata,
        turn_scope_id=turn_scope_id,
        runtime_state=runtime_state,
        pending_response=seed_response,
    )
