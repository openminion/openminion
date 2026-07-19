import json
import mimetypes
from typing import Any, Callable, Literal, cast

from openminion.modules.brain.retry import STRUCTURED_RETRY_MESSAGE_HINT
from openminion.modules.tool.schema_service import ToolSchemaService


_TOOL_SCHEMA_SERVICE = ToolSchemaService()
_MessageRole = Literal["system", "user", "assistant", "tool"]


def _messages_from_context(context: dict[str, Any]) -> list[Any]:
    from openminion.modules.llm.schemas import Message
    from openminion.modules.llm.schemas import ImageContentPart, TextContentPart

    turns = context.get("turns", []) if isinstance(context.get("turns"), list) else []
    turn_messages: list[Any] = []
    for turn in turns:
        role = ""
        content = ""
        attachments: list[str] = []
        if isinstance(turn, dict):
            role = str(turn.get("role", "")).strip().lower()
            content = str(turn.get("content", "")).strip()
            raw_attachments = turn.get("attachments", [])
            if isinstance(raw_attachments, list):
                attachments = [
                    str(item).strip() for item in raw_attachments if str(item).strip()
                ]
        else:
            role = str(getattr(turn, "role", "")).strip().lower()
            content = str(getattr(turn, "content", "")).strip()
            raw_attachments = getattr(turn, "attachments", [])
            if isinstance(raw_attachments, list):
                attachments = [
                    str(item).strip() for item in raw_attachments if str(item).strip()
                ]
        if role == "agent":
            role = "assistant"
        if role not in {"system", "user", "assistant", "tool"}:
            role = "user"
        content_parts: list[Any] = []
        if content:
            content_parts.append(
                TextContentPart(
                    text=content,
                    block_kind="turn_input",
                    segment_ids=[],
                )
            )
        for attachment in attachments:
            mime = str(mimetypes.guess_type(attachment)[0] or "").strip().lower()
            if not mime.startswith("image/"):
                continue
            content_parts.append(
                ImageContentPart(
                    source="path",
                    path=attachment,
                    mime_type=mime,
                    block_kind="turn_input",
                )
            )
        if content_parts:
            turn_messages.append(
                Message(
                    role=cast(_MessageRole, role),
                    content=content,
                    content_parts=content_parts,
                )
            )
        elif content:
            turn_messages.append(
                Message(role=cast(_MessageRole, role), content=content)
            )

    pack_messages = context.get("messages", [])
    normalized_pack_messages: list[Any] = []
    for message in pack_messages:
        if not isinstance(message, dict):
            normalized_pack_messages.append(message)
            continue
        raw_message = dict(message)
        raw_meta = raw_message.get("meta")
        if (
            isinstance(raw_meta, dict)
            and raw_message.get("content")
            and not raw_message.get("content_parts")
        ):
            block_kind = cast(Any, str(raw_meta.get("block_kind", "")).strip() or None)
            raw_message["content_parts"] = [
                TextContentPart(
                    text=str(raw_message.get("content", "")),
                    block_kind=block_kind,
                    cache_eligible=bool(raw_meta.get("cache_eligible", False)),
                    segment_ids=[
                        str(item).strip()
                        for item in raw_meta.get("segment_ids", [])
                        if str(item).strip()
                    ],
                    refs=[
                        str(item).strip()
                        for item in raw_meta.get("refs", [])
                        if str(item).strip()
                    ],
                ).model_dump()
            ]
        normalized_pack_messages.append(raw_message)

    messages = [Message.model_validate(m) for m in normalized_pack_messages]
    if messages:
        system_messages = [
            message
            for message in messages
            if str(getattr(message, "role", "")).strip().lower() == "system"
        ]
        conversational_messages = [
            message
            for message in messages
            if str(getattr(message, "role", "")).strip().lower() != "system"
        ]
        if turn_messages and len(conversational_messages) <= 1:
            return [*system_messages, *turn_messages]
        return messages

    if turn_messages:
        return turn_messages

    hints = context.get("hints", {}) if isinstance(context.get("hints"), dict) else {}
    user_input = str(hints.get("user_input", "")).strip()
    if user_input:
        return [Message(role="user", content=user_input)]

    serialized_context = _serialize_context_fallback(context)
    if serialized_context:
        return [Message(role="user", content=serialized_context)]
    return [Message(role="user", content="structured_context_unavailable")]


def _serialize_context_fallback(context: dict[str, Any]) -> str:
    payload: dict[str, Any] = {}
    for key in ("subtasks", "results", "objective", "goal", "instruction"):
        value = context.get(key)
        if value not in (None, "", [], {}):
            payload[key] = value
    hints = context.get("hints")
    if isinstance(hints, dict):
        for key in ("instruction", "original_user_input", "summary"):
            value = hints.get(key)
            if value not in (None, "", [], {}):
                payload[f"hints.{key}"] = value
    if not payload:
        payload = {
            key: value
            for key, value in context.items()
            if key != "hints" and value not in (None, "", [], {})
        }
    if not payload:
        return ""
    try:
        return json.dumps(payload, sort_keys=True, ensure_ascii=True, default=str)
    except Exception:
        return str(payload)


def _insert_retry_system_message(
    messages: list[Any], *, retry_message: str
) -> list[Any]:
    from openminion.modules.llm.schemas import Message

    if not retry_message:
        return messages

    retry = Message(role="system", content=retry_message)
    insert_at = 0
    while insert_at < len(messages):
        role = str(getattr(messages[insert_at], "role", "")).strip().lower()
        if role != "system":
            break
        insert_at += 1
    updated = list(messages)
    updated.insert(insert_at, retry)
    return updated


def _append_system_messages(messages: list[Any], *system_messages: str) -> list[Any]:
    updated = list(messages)
    for retry_message in system_messages:
        updated = _insert_retry_system_message(
            updated,
            retry_message=retry_message,
        )
    return updated


def _context_manifest(context: dict[str, Any]) -> dict[str, Any]:
    manifest = context.get("context_manifest")
    return dict(manifest) if isinstance(manifest, dict) else {}


def _maybe_set_string_metadata(
    metadata: dict[str, Any],
    *,
    key: str,
    value: Any,
) -> None:
    normalized = str(value or "").strip()
    if normalized:
        metadata[key] = normalized


def _maybe_set_metadata(
    metadata: dict[str, Any],
    *,
    key: str,
    value: Any,
) -> None:
    if value not in (None, "", [], {}):
        metadata[key] = value


def _maybe_set_positive_int_metadata(
    metadata: dict[str, Any],
    *,
    key: str,
    value: Any,
) -> None:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return
    if parsed > 0:
        metadata[key] = parsed


def _request_metadata(
    *,
    purpose: str,
    context: dict[str, Any],
    hints: dict[str, Any],
    mode_name: str | None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {"purpose": purpose}
    manifest = _context_manifest(context)

    _maybe_set_positive_int_metadata(
        metadata,
        key="timeout_seconds",
        value=hints.get("structured_timeout_seconds"),
    )
    for key in ("prompt_cache_key", "static_prefix_hash"):
        _maybe_set_string_metadata(metadata, key=key, value=manifest.get(key))
    if mode_name:
        metadata["mode_name"] = mode_name

    for metadata_key, hint_key in (
        ("thinking_requested_profile", "thinking_requested_profile"),
        ("thinking_reasoning_profile", "thinking_effective_profile"),
        ("thinking_source_layer", "thinking_source_layer"),
        ("thinking_degraded_reason", "thinking_degraded_reason"),
        ("thinking", "thinking_provider_effort"),
        ("thinking_mode_name", "thinking_mode_name"),
        ("thinking_mode_default_profile", "thinking_mode_default_profile"),
    ):
        _maybe_set_string_metadata(
            metadata,
            key=metadata_key,
            value=hints.get(hint_key),
        )

    for metadata_key in (
        "thinking_degraded_reasons",
        "thinking_mode_allowed_profiles",
    ):
        _maybe_set_metadata(
            metadata,
            key=metadata_key,
            value=hints.get(metadata_key),
        )

    if "thinking_mode_request_override_allowed" in hints:
        metadata["thinking_mode_request_override_allowed"] = hints.get(
            "thinking_mode_request_override_allowed"
        )
    return metadata


def _build_compound_intent_guidance_message(*, purpose: str, schema: type) -> str:
    if purpose != "decide":
        return ""
    if str(getattr(schema, "__name__", "")).strip() != "Decision":
        return ""
    return "\n".join(
        [
            "When the request includes multiple distinct goals or deliverables, populate Decision.sub_intents as an ordered list of short strings.",
            "Use sub_intents only for genuinely compound requests; leave it empty for simple single-goal requests.",
            "Keep sub_intents compact and schema-valid. Do not invent extra structure beyond the declared list of strings.",
        ]
    )


def _build_pending_turn_context_guidance_message(*, purpose: str, schema: type) -> str:
    if purpose != "decide":
        return ""
    if str(getattr(schema, "__name__", "")).strip() != "Decision":
        return ""
    return "\n".join(
        [
            "When your assistant response offers a concrete next action and expects a short confirmation follow-up, populate Decision.pending_turn_context.",
            "Also populate Decision.pending_turn_context after a substantive plan, itinerary, roadmap, or multi-step answer when a short same-session referential follow-up is likely (for example: detail each day, break down each step, expand that section).",
            "Also populate Decision.pending_turn_context after a substantive entity-establishing or concept-establishing answer when a short same-session omitted-subject follow-up is likely (for example: latest price, market cap, who created it, how does it compare).",
            "Use original_user_request for the user request that led to the offer.",
            "Use active_work_summary for the offered next action, and known_context for already-known typed facts.",
            "Leave pending_turn_context unset when no carry-forward is needed; do not rely on hidden prose markers or tags.",
        ]
    )


def _build_clarify_context_guidance_message(*, purpose: str, schema: type) -> str:
    if purpose != "decide":
        return ""
    if str(getattr(schema, "__name__", "")).strip() != "Decision":
        return ""
    return "\n".join(
        [
            'When your response asks the user for missing information, preferences, or decisions before you can act, use respond_kind="clarify" and populate clarify_context.',
            "Set question to the same user-facing clarification question(s) you are asking.",
            "Set clarify_context.original_user_input to the user's original request.",
            "Set clarify_context.clarify_question to the question(s) you are asking.",
            "Set clarify_context.known_context with any facts already established.",
            "Set clarify_context.inferred_goal to your best understanding of what the user wants.",
            'Do not use respond_kind="answer" when the response expects the user to provide missing details needed for execution.',
            "The clarify rail ensures the next turn retains the original request context even when the user reply is short.",
        ]
    )


def _build_request_readiness_guidance_message(*, purpose: str, schema: type) -> str:
    if purpose != "decide":
        return ""
    if str(getattr(schema, "__name__", "")).strip() != "Decision":
        return ""
    return "\n".join(
        [
            "When the request may continue beyond a simple answer, populate Decision.request_readiness.",
            "Set posture to direct, brief_plan, or review_before_act based on the smallest safe amount of planning.",
            "Set requested_outcome to answer_only, plan_only, review_only, or execute as the maximum user-authorized outcome.",
            "Set state to ready only when the next step can proceed without clarification, plan review, or operation approval.",
            "Use needs_user only for blocker information; otherwise proceed with bounded reversible assumptions.",
        ]
    )


def _build_pending_conversational_clarification_followup_guidance_message(
    *,
    purpose: str,
    schema: type,
    hints: dict[str, Any] | None,
) -> str:
    """Build pending conversational clarification followup guidance message helper."""

    if purpose != "decide":
        return ""
    if str(getattr(schema, "__name__", "")).strip() != "Decision":
        return ""
    if not isinstance(hints, dict):
        return ""
    pending = hints.get("pending_conversational_clarification")
    if not isinstance(pending, dict) or not pending:
        return ""
    return "\n".join(
        [
            "A pending_conversational_clarification context is present in this turn's hints.",
            "Treat the current user reply as the answer to the recorded unresolved_question.",
            "Anchor route, respond_kind, and clarify_context decisions to original_user_input plus the user's reply; do not treat the short reply as a fresh, standalone request.",
            "If original_user_input was an actionable request, route the decision so that request can now be completed using the answered clarification.",
            "If the reply still does not fully resolve unresolved_question, ask the next narrowest clarification rather than restarting the conversation.",
        ]
    )


def _build_task_plan_guidance_message_verbose_weak_model(
    *, purpose: str, schema: type
) -> str:
    """PTCH Phase 4: variant for weak models (MiniMax, etc.)."""
    if purpose != "decide":
        return ""
    if str(getattr(schema, "__name__", "")).strip() != "Decision":
        return ""
    base = _build_task_plan_guidance_message(purpose=purpose, schema=schema)
    if not base:
        return ""
    return "\n".join(
        [
            base,
            "CRITICAL emission rules for this agent (variant=verbose_weak_model):",
            "For multi-step work, do not write XML task-plan trailers. Route to act so the adaptive loop can call the plan loop-control tool.",
            "The plan tool owns declare, step_completed, step_blocked, revise, abandon, and complete actions.",
            "A Markdown plan without the plan tool is not durable cross-turn state.",
        ]
    )


_TRAILER_GUIDANCE_VARIANT_BUILDERS: dict[str, dict[str, Callable[..., str]]] = {
    "apd": {
        "verbose_weak_model": _build_task_plan_guidance_message_verbose_weak_model,
    },
}


def _select_task_plan_guidance(
    *,
    purpose: str,
    schema: type,
    variant_map: dict[str, str] | None,
) -> str:
    """PTCH Phase 4: select base or variant APD guidance.

    Unknown variants fall back to base with a logged warning.
    """
    if isinstance(variant_map, dict):
        variant_name = str(variant_map.get("apd") or "").strip()
    else:
        variant_name = ""
    if not variant_name:
        return _build_task_plan_guidance_message(purpose=purpose, schema=schema)
    lane_variants = _TRAILER_GUIDANCE_VARIANT_BUILDERS.get("apd", {})
    builder = lane_variants.get(variant_name)
    if builder is None:
        import logging

        logging.getLogger(__name__).warning(
            "unknown trailer_guidance_variant for lane=apd variant=%s; falling back to base guidance",
            variant_name,
        )
        return _build_task_plan_guidance_message(purpose=purpose, schema=schema)
    return builder(purpose=purpose, schema=schema)


def _build_task_plan_guidance_message(*, purpose: str, schema: type) -> str:
    if purpose != "decide":
        return ""
    if str(getattr(schema, "__name__", "")).strip() != "Decision":
        return ""
    return "\n".join(
        [
            "For complex multi-turn work, route to act so the adaptive loop can call the plan loop-control tool.",
            "Do not emit task-plan XML trailers or rely on Markdown-only plans for durable state.",
            "The plan tool records declare, step_completed, step_blocked, revise, abandon, and complete actions with bounded tool_families.",
        ]
    )


def _build_research_profile_guidance_message(*, purpose: str, schema: type) -> str:
    if purpose != "decide":
        return ""
    if str(getattr(schema, "__name__", "")).strip() != "Decision":
        return ""
    return "\n".join(
        [
            'Use the entry `research` control tool / `act_profile="research"` when the user explicitly wants deep research, iterative discovery, multiple research passes, or evidence gathering and synthesis across multiple searches before answering.',
            'Examples that should usually use the entry `research` control tool / `act_profile="research"`: "deep research", "iterate twice", "do a deep dive", "gather the latest news and build a researched stock basket", or any request that asks for multi-source discovery before a final synthesis.',
            "For a single research brief or discovery thread, prefer the entry research control tool instead of calling the decompose control tool.",
            "Call the decompose control tool only when the work truly splits into independent subtasks or separate deliverables that should be orchestrated individually.",
            'Use `act_profile="general"` for ordinary single-pass act work that does not require a dedicated iterative research loop.',
            "Do not leave deep-research requests on the default general act loop when the need for iterative research is already semantically clear.",
        ]
    )


def _build_request(
    *,
    model: str,
    purpose: str,
    context: dict[str, Any],
    schema: type,
    temperature: float,
) -> Any:
    from openminion.modules.llm.schemas import LLMRequest, ToolSpec

    hints = context.get("hints", {}) if isinstance(context.get("hints"), dict) else {}
    user_input = str(hints.get("user_input", "")).strip()
    mode_name = str(hints.get("mode_name", "")).strip().lower() or None
    bundle = _TOOL_SCHEMA_SERVICE.get_tools_for_purpose(
        purpose=purpose,
        query=user_input,
        caller_context="llm_request",
        execution_tools=[],
        structured_schema=schema,
        prompt_schemas_enabled=False,
    )

    messages = _append_system_messages(
        list(_messages_from_context(context)),
        str(hints.get(STRUCTURED_RETRY_MESSAGE_HINT, "")).strip(),
        _build_compound_intent_guidance_message(
            purpose=purpose,
            schema=schema,
        ),
        _build_pending_turn_context_guidance_message(
            purpose=purpose,
            schema=schema,
        ),
        _build_clarify_context_guidance_message(
            purpose=purpose,
            schema=schema,
        ),
        _build_request_readiness_guidance_message(
            purpose=purpose,
            schema=schema,
        ),
        _build_pending_conversational_clarification_followup_guidance_message(
            purpose=purpose,
            schema=schema,
            hints=hints,
        ),
    )
    variant_map_hint = (
        hints.get("trailer_guidance_variant")
        if isinstance(hints.get("trailer_guidance_variant"), dict)
        else None
    )
    messages = _append_system_messages(
        messages,
        _select_task_plan_guidance(
            purpose=purpose,
            schema=schema,
            variant_map=variant_map_hint,
        ),
        _build_research_profile_guidance_message(
            purpose=purpose,
            schema=schema,
        ),
    )

    request = LLMRequest(
        messages=messages,
        model=model,
        temperature=temperature,
        metadata=_request_metadata(
            purpose=purpose,
            context=context,
            hints=hints,
            mode_name=mode_name,
        ),
    )
    request.tools = [
        ToolSpec(
            name=str(item.get("name", "")).strip(),
            description=str(item.get("description", "")).strip(),
            input_schema=(
                dict(item.get("parameters", {}))
                if isinstance(item.get("parameters"), dict)
                else {}
            ),
        )
        for item in list(bundle.system_tools)
        if str(item.get("name", "")).strip()
    ]
    request.tool_choice = {"type": "function", "function": {"name": "submit_output"}}
    return request


__all__ = [
    "STRUCTURED_RETRY_MESSAGE_HINT",
    "_TOOL_SCHEMA_SERVICE",
    "_build_request",
    "_insert_retry_system_message",
    "_messages_from_context",
]
