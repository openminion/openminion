from __future__ import annotations

from typing import Any

from openminion.modules.telemetry.trace.structured import write_structured_trace

from .act_profiles import fixed_act_profile_from_context
from .adapters.llm.model_profiles import (
    RetryStrategy,
    resolve_capability_profile_for_context,
)
from .interfaces import LLMAPI
from .schemas.simple import (
    SimplifiedDecision,
    UltraSimpleDecision,
    promote_to_full_decision,
)

STRUCTURED_RETRYABLE_KEY = "_structured_retryable"
STRUCTURED_FAILURE_KIND_KEY = "_structured_failure_kind"
STRUCTURED_HAS_TOOL_CALLS_KEY = "_structured_has_tool_calls"
STRUCTURED_RETRY_MESSAGE_HINT = "structured_retry_message"
_DECISION_SCHEMA_NAMES = {
    "Decision",
    "SimplifiedDecision",
    "UltraSimpleDecision",
}
_RETRY_NUDGE_STYLES = {
    "openai_function_calling": (
        " Return only the structured JSON object for the active schema and do not "
        "wrap it in markdown or prose."
    ),
    "json_body_first": (
        " If tool calling is unavailable, put the JSON object directly in the "
        "response body with no surrounding explanation."
    ),
}


def build_entry_retry_message(*, has_real_tools: bool) -> str:
    if has_real_tools:
        return (
            "Your previous unified entry response was invalid. Retry now. In this "
            "step you may do exactly one of the following: call a real tool, call "
            "clarify(question=...), or answer directly with plain text. Do not emit "
            "submit_output, mode labels, act_profile, execution_target, or other "
            "decide metadata."
        )
    return (
        "Your previous unified entry response was invalid. Retry now. In this step "
        "you may either call clarify(question=...) or answer directly with plain "
        "text. Do not emit submit_output, mode labels, act_profile, or other "
        "decide metadata."
    )


def _model_json_schema(schema: Any) -> dict[str, Any]:
    getter = getattr(schema, "model_json_schema", None)
    if not callable(getter):
        return {}
    try:
        payload = getter()
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    return dict(payload)


def _schema_property_names(
    payload: dict[str, Any], *, omit_fields: set[str] | None = None
) -> list[str]:
    properties = payload.get("properties")
    if not isinstance(properties, dict):
        return []
    names: list[str] = []
    for raw_name in properties:
        name = str(raw_name).strip()
        if name and name not in (omit_fields or set()):
            names.append(name)
    return names


def _schema_required_names(
    payload: dict[str, Any], *, omit_fields: set[str] | None = None
) -> list[str]:
    required = payload.get("required")
    if not isinstance(required, list):
        return []
    names: list[str] = []
    for raw_name in required:
        name = str(raw_name).strip()
        if name and name not in (omit_fields or set()):
            names.append(name)
    return names


def _render_schema_type(spec: dict[str, Any]) -> str:
    direct_type = str(spec.get("type", "")).strip()
    if direct_type:
        return direct_type
    any_of = spec.get("anyOf")
    if not isinstance(any_of, list):
        return ""
    rendered: list[str] = []
    for item in any_of:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type", "")).strip()
        if item_type and item_type not in rendered:
            rendered.append(item_type)
    return "|".join(rendered)


def _schema_type_hints(
    payload: dict[str, Any], *, omit_fields: set[str] | None = None
) -> list[str]:
    properties = payload.get("properties")
    if not isinstance(properties, dict):
        return []
    hints: list[str] = []
    for raw_name, raw_spec in properties.items():
        name = str(raw_name).strip()
        if not name or name in (omit_fields or set()) or not isinstance(raw_spec, dict):
            continue
        rendered_type = _render_schema_type(raw_spec)
        if rendered_type:
            hints.append(f"{name}={rendered_type}")
    return hints


def _schema_enum_hints(
    payload: dict[str, Any], *, omit_fields: set[str] | None = None
) -> list[str]:
    properties = payload.get("properties")
    if not isinstance(properties, dict):
        return []
    hints: list[str] = []
    for raw_name, raw_spec in properties.items():
        name = str(raw_name).strip()
        if not name or name in (omit_fields or set()) or not isinstance(raw_spec, dict):
            continue
        enum_values = raw_spec.get("enum")
        if not isinstance(enum_values, list) or not enum_values:
            continue
        rendered = [str(item).strip() for item in enum_values if str(item).strip()]
        if rendered:
            hints.append(f"{name}={'|'.join(rendered[:6])}")
    return hints


def _schema_retry_guidance(schema: Any, *, omit_fields: set[str] | None = None) -> str:
    payload = _model_json_schema(schema)
    if not payload:
        return ""
    parts: list[str] = []
    title = str(payload.get("title", "")).strip()
    if title:
        parts.append(f"Schema: {title}.")
    allowed_keys = _schema_property_names(payload, omit_fields=omit_fields)
    if allowed_keys:
        parts.append(f"Schema keys: {', '.join(allowed_keys[:10])}.")
    required_keys = _schema_required_names(payload, omit_fields=omit_fields)
    if required_keys:
        parts.append(f"Required schema keys: {', '.join(required_keys[:10])}.")
    if payload.get("additionalProperties") is False:
        parts.append("Do not include keys outside this schema.")
    type_hints = _schema_type_hints(payload, omit_fields=omit_fields)
    if type_hints:
        parts.append(f"Schema types: {'; '.join(type_hints[:6])}.")
    enum_hints = _schema_enum_hints(payload, omit_fields=omit_fields)
    if enum_hints:
        parts.append(f"Schema enums: {'; '.join(enum_hints[:4])}.")
    return " ".join(parts)


def _finalize_retry_message(
    base_message: str,
    *,
    schema: Any | None,
    style_guidance: str,
    omit_schema_fields: set[str] | None = None,
) -> str:
    parts = [base_message]
    schema_guidance = _schema_retry_guidance(schema, omit_fields=omit_schema_fields)
    if schema_guidance:
        parts.append(schema_guidance)
    if style_guidance:
        parts.append(style_guidance.strip())
    return " ".join(part.strip() for part in parts if str(part).strip())


def _retry_message_from_context(context: dict[str, Any]) -> str:
    hints = context.get("hints")
    if not isinstance(hints, dict):
        return ""
    return str(hints.get(STRUCTURED_RETRY_MESSAGE_HINT, "") or "")


def build_structured_retry_message(
    *,
    schema_name: str,
    has_prior_results: bool,
    retry_nudge_style: str = "",
    schema: Any | None = None,
    default_act_profile: str | None = None,
) -> str:
    style_guidance = _RETRY_NUDGE_STYLES.get(str(retry_nudge_style or "").strip(), "")
    omit_schema_fields: set[str] = set()
    if default_act_profile is not None:
        omit_schema_fields.add("act_profile")
    if schema_name in {"Decision", "_ActPayload"}:
        omit_schema_fields.update({"reason_code", "confidence"})
    if schema_name == "Decision":
        retry_nudge = (
            "Use the existing tool result already present in context to close the turn. "
            "Do not repeat a tool call that already ran unless the prior result is unusable."
            if has_prior_results
            else "For simple tool-backed factual asks, prefer mode='act' when one "
            "same-turn local act loop can fully satisfy the request."
        )
        act_payload_guidance = (
            "execution_target (for act). "
            "The runtime assigns act_profile from config, so do not emit "
            "act_profile yourself. "
            if default_act_profile is not None
            else "act_profile/execution_target (for act). "
        )
        if default_act_profile == "orchestrate":
            orchestrate_guidance = (
                "Because the runtime assigns act_profile='orchestrate' for this "
                "agent, include a non-empty subtasks list whenever you choose "
                "mode='act'. "
            )
        elif default_act_profile is not None:
            orchestrate_guidance = ""
        else:
            orchestrate_guidance = (
                "When act_profile='orchestrate', also include a non-empty "
                "subtasks list. "
            )
        compound_guidance = (
            "For compound or partially supported requests, use mode='act' with "
            "sub_intents instead of explaining capability gaps in prose. "
            if default_act_profile is not None
            else "For compound or partially supported requests, use mode='act' with "
            "act_profile='orchestrate' and sub_intents instead of explaining "
            "capability gaps in prose. "
        )
        base_message = (
            "Your previous submit_output payload was invalid for Decision schema. "
            "Retry now and call submit_output with a JSON object containing: "
            "mode plus the lower-layer payload fields required by the selected "
            "mode. Optional metadata fields such as confidence, reason_code, "
            "sub_intents, and rationale may be included when available, but they "
            "are not required to start ordinary work. For the built-in modes this "
            "means: respond_kind/answer/question (for respond), "
            f"{act_payload_guidance}"
            f"{orchestrate_guidance}"
            "Do not emit direct tool calls in decide mode. If the user explicitly "
            "asks to hand work to another agent, keep mode='act' and express that "
            "through execution_target.kind='delegated' plus target_agent_id or "
            "target_capability. "
            "Do not claim tools are unavailable in decide mode. "
            "Tool capabilities are provided in the decide prompt context. "
            "Execution tools are available in act phase. "
            f"{compound_guidance}"
            f"{retry_nudge}"
        )
        return _finalize_retry_message(
            base_message,
            schema=schema,
            style_guidance=style_guidance,
            omit_schema_fields=omit_schema_fields,
        )

    if schema_name == "_ActPayload":
        act_fields_guidance = (
            "execution_target when delegation is explicit, and optional "
            "rationale/max_steps_hint/subtasks. "
            "Do not include act_profile; the runtime assigns it from config. "
            if default_act_profile is not None
            else "optional act_profile, execution_target when delegation is explicit, "
            "and optional rationale/max_steps_hint/subtasks. "
        )
        base_message = (
            "Your previous response did not return a valid act payload. "
            "Retry now and call submit_output with a JSON object containing only "
            f"{act_fields_guidance}"
            "Do not return mode, commands, next_action, continue/replan closures, "
            "or plain-text limitation statements here. If the user explicitly "
            "requested delegation, set execution_target.kind='delegated' and "
            "provide target_agent_id or target_capability. Tool choice happens "
            "inside the act loop, not in the decide payload."
        )
        return _finalize_retry_message(
            base_message,
            schema=schema,
            style_guidance=style_guidance,
            omit_schema_fields=omit_schema_fields,
        )

    if schema_name == "_RespondPayload":
        base_message = (
            "Your previous response did not return a valid respond payload. "
            "Retry now and call submit_output with a JSON object containing only "
            "respond_kind plus the matching content field. If respond_kind='answer', "
            "you must include answer with the full user-facing reply and leave "
            "question empty. If respond_kind='clarify', you must include question "
            "with the clarification request and leave answer empty. Do not include "
            "mode, sub_intents, rationale, commands, plan fields, or any other "
            "top-level decision fields in this payload. Do not respond in plain text."
        )
        return _finalize_retry_message(
            base_message,
            schema=schema,
            style_guidance=style_guidance,
            omit_schema_fields=omit_schema_fields,
        )

    if schema_name == "PostActionJudgment":
        base_message = (
            "Your previous response did not return a valid PostActionJudgment. "
            "Retry now and call submit_output with a JSON object containing only "
            "outcome, reason, user_message, and optional confidence. outcome must "
            "be exactly one of: advance, retry, replan, ask_user, halt, or skip. "
            "If you include confidence, it must be a numeric value between 0.0 and "
            "1.0 or be omitted/null. "
            "Do not answer the user directly outside user_message. Do not emit "
            "mode, commands, plan fields, tool calls, or plain-text commentary "
            "outside the structured judgment object."
        )
        return _finalize_retry_message(
            base_message,
            schema=schema,
            style_guidance=style_guidance,
            omit_schema_fields=omit_schema_fields,
        )

    if schema_name == "ClosureJudgment":
        base_message = (
            "Your previous response did not return a valid ClosureJudgment. "
            "Retry now and call submit_output with a JSON object containing only "
            "satisfied, reason, next_action, final_answer, and optional "
            "post_completion_critique. next_action must be exactly one of: close, "
            "continue, or replan. If next_action='close', include the concise "
            "user-facing reply in final_answer. If not closing, set final_answer "
            "to null or an empty string. If you include post_completion_critique, "
            "it must contain intent_id, summary, lessons, and optional "
            "next_time_action, and intent_id must exactly match one typed intent "
            "outcome from this turn. Do not emit mode, tool calls, commands, or "
            "free-form prose outside the structured judgment object."
        )
        return _finalize_retry_message(
            base_message,
            schema=schema,
            style_guidance=style_guidance,
        )

    extra_guidance = ""
    if schema_name == "Plan":
        extra_guidance = (
            " For plan mode specifically: return a structured plan for the viable "
            "subset only, preserve declared sub-intent IDs, and do not explain "
            "capability gaps in prose here. Required top-level fields are: "
            "objective, steps, stop_conditions, assumptions, risk_summary, and "
            "success_criteria. Each step must include kind, title, and any command "
            "fields needed for that step. If some work is blocked, you may include "
            "a finish step or other non-executable step tagged with that "
            "sub_intent_ids entry so later feasibility handling can surface it. "
            "Do not emit unresolved placeholders or sentinels in any step. "
            "For example, never return `{{...}}`, bracket placeholders like "
            "`[SUMMARY]`, or tool arguments such as `<UNKNOWN>`. "
            "Any finish step final_message must be user-ready exactly as written."
        )
    return _finalize_retry_message(
        (
            f"Your previous response did not return a valid {schema_name} "
            "submit_output payload. Retry now and call submit_output with a "
            "JSON object that matches the required schema exactly. Do not "
            f"respond in plain text.{extra_guidance}"
        ),
        schema=schema,
        style_guidance=style_guidance,
        omit_schema_fields=omit_schema_fields,
    )


def is_retryable_structured_result(
    result: Any,
    *,
    schema_name: str,
    context: dict[str, Any],
) -> bool:
    if not isinstance(result, dict):
        return False
    del schema_name, context
    return bool(result.get(STRUCTURED_RETRYABLE_KEY))


def strip_structured_retry_metadata(result: Any) -> Any:
    if not isinstance(result, dict):
        return result
    normalized = {
        key: value
        for key, value in result.items()
        if not key.startswith("_structured_")
    }
    route = str(normalized.get("route", "") or "").strip()
    mode = str(normalized.get("mode", "") or "").strip()
    if route and not mode:
        normalized["mode"] = route
    elif mode and not route:
        normalized["route"] = mode
    return normalized


def build_decide_fail_closed_result(result: Any) -> dict[str, Any]:
    reason_code = "invalid_decide_structured_output"
    if isinstance(result, dict):
        failure_kind = str(result.get(STRUCTURED_FAILURE_KIND_KEY, "")).strip()
        if failure_kind in {
            "invalid_decide_structured_output",
            "invalid_decide_tool_call",
        }:
            reason_code = failure_kind
        elif bool(result.get(STRUCTURED_HAS_TOOL_CALLS_KEY)):
            reason_code = "invalid_decide_tool_call"
    return {
        "route": "respond",
        "mode": "respond",
        "confidence": 0.3,
        "reason_code": reason_code,
        "respond_kind": "answer",
        "sub_intents": [],
        "rationale": "",
        "answer": (
            "I hit an internal decision error before I could continue safely on "
            "this turn."
        ),
    }


def add_retry_instruction_to_context(
    *,
    context: dict[str, Any],
    schema_name: str,
    retry_nudge_style: str = "",
    schema: Any | None = None,
) -> dict[str, Any]:
    retry_context = dict(context)
    hints = dict(
        context.get("hints", {}) if isinstance(context.get("hints"), dict) else {}
    )
    hints[STRUCTURED_RETRY_MESSAGE_HINT] = build_structured_retry_message(
        schema_name=schema_name,
        has_prior_results=bool(hints.get("has_prior_results")),
        retry_nudge_style=retry_nudge_style,
        schema=schema,
        default_act_profile=fixed_act_profile_from_context(context),
    )
    retry_context["hints"] = hints
    return retry_context


def _schema_name(schema: Any) -> str:
    return str(getattr(schema, "__name__", "") or "")


def _is_decision_schema(schema_name: str) -> bool:
    return schema_name in _DECISION_SCHEMA_NAMES


def _max_attempts_for_schema(
    schema_name: str, *, context: dict[str, Any], model: str
) -> int:
    if schema_name == "ClosureJudgment":
        return 1
    if not _is_decision_schema(schema_name):
        return 2
    profile = resolve_capability_profile_for_context(model_name=model, context=context)
    return max(1, int(profile.max_structured_retries or 1))


def _schema_sequence(
    *,
    schema: Any,
    schema_name: str,
    context: dict[str, Any],
    model: str,
) -> list[Any]:
    max_attempts = _max_attempts_for_schema(schema_name, context=context, model=model)
    profile = resolve_capability_profile_for_context(model_name=model, context=context)
    if (
        not _is_decision_schema(schema_name)
        or profile.retry_strategy != RetryStrategy.PROGRESSIVE_SIMPLIFICATION
    ):
        return [schema for _ in range(max_attempts)]
    sequence: list[Any] = [schema]
    if schema_name == "Decision":
        sequence.extend([SimplifiedDecision, UltraSimpleDecision])
    elif schema_name == "SimplifiedDecision":
        sequence.append(UltraSimpleDecision)
    else:
        sequence.extend([schema for _ in range(max_attempts - 1)])
    return sequence[:max_attempts]


def _finalize_result_for_schema(*, schema_name: str, result: Any) -> Any:
    if not isinstance(result, dict):
        return result
    if schema_name in {"SimplifiedDecision", "UltraSimpleDecision"}:
        promoted = promote_to_full_decision(
            result,
            2 if schema_name == "SimplifiedDecision" else 3,
        )
        if promoted is not None:
            return promoted
        return {
            STRUCTURED_RETRYABLE_KEY: True,
            STRUCTURED_FAILURE_KIND_KEY: "unsynthesizable_action_mode",
        }
    return result


def _optional_empty_result_for_schema(schema_name: str) -> dict[str, Any] | None:
    if schema_name == "UserMessageCandidateReport":
        return {
            "session_id": None,
            "agent_id": None,
            "items": [],
        }
    return None


def _last_trace_context_for_llm_api(llm_api: LLMAPI) -> dict[str, Any] | None:
    getter = getattr(llm_api, "get_last_trace_context", None)
    if callable(getter):
        trace_context = getter()
        if isinstance(trace_context, dict):
            return dict(trace_context)
    trace_context = getattr(llm_api, "_last_trace_context", None)
    if isinstance(trace_context, dict):
        return dict(trace_context)
    return None


def _write_retry_trace(
    llm_api: LLMAPI,
    *,
    schema_name: str,
    schema_sequence: list[Any],
    attempt_index: int,
    profile: Any,
    retry_message: str,
    result: Any | None,
    outcome: str,
    fail_closed_reason: str = "",
) -> None:
    trace_context = _last_trace_context_for_llm_api(llm_api)
    if not trace_context:
        return
    result_payload = result if isinstance(result, dict) else {}
    write_structured_trace(
        trace_context=trace_context,
        patch={
            "retry": {
                "attempt_index": int(attempt_index),
                "schema_name": schema_name,
                "schema_sequence": [_schema_name(item) for item in schema_sequence],
                "retry_strategy": str(
                    getattr(profile, "retry_strategy", "") or ""
                ).strip(),
                "retry_nudge_style": str(
                    getattr(profile, "retry_nudge_style", "") or ""
                ).strip(),
                "retry_message": retry_message,
                "outcome": outcome,
                "result_failure_kind": str(
                    result_payload.get(STRUCTURED_FAILURE_KIND_KEY, "") or ""
                ),
                "fail_closed_reason": str(fail_closed_reason or ""),
            }
        },
    )


def call_structured_with_retry(
    llm_api: LLMAPI,
    *,
    model: str,
    purpose: str,
    context: dict[str, Any],
    schema: Any,
    temperature: float = 0.0,
) -> Any:
    schema_name = str(getattr(schema, "__name__", "") or "")
    del temperature
    profile = resolve_capability_profile_for_context(model_name=model, context=context)
    schema_sequence = _schema_sequence(
        schema=schema,
        schema_name=schema_name,
        context=context,
        model=model,
    )

    def _write_attempt_trace(
        *,
        active_schema: Any,
        active_context: dict[str, Any],
        attempt_index: int,
        result: Any | None,
        outcome: str,
        fail_closed_reason: str = "",
    ) -> None:
        _write_retry_trace(
            llm_api,
            schema_name=_schema_name(active_schema),
            schema_sequence=schema_sequence,
            attempt_index=attempt_index,
            profile=profile,
            retry_message=_retry_message_from_context(active_context),
            result=result,
            outcome=outcome,
            fail_closed_reason=fail_closed_reason,
        )

    attempt_index = 0
    active_context = context
    active_schema = schema_sequence[attempt_index]
    result = llm_api.call_structured(
        model=model,
        purpose=purpose,
        context=active_context,
        schema=active_schema,
    )
    _write_attempt_trace(
        active_schema=active_schema,
        active_context=active_context,
        attempt_index=attempt_index,
        result=result,
        outcome=(
            "retryable"
            if is_retryable_structured_result(
                result,
                schema_name=_schema_name(active_schema),
                context=active_context,
            )
            else "accepted"
        ),
    )

    while True:
        active_schema_name = _schema_name(active_schema)
        if not is_retryable_structured_result(
            result,
            schema_name=active_schema_name,
            context=active_context,
        ):
            finalized = _finalize_result_for_schema(
                schema_name=active_schema_name,
                result=result,
            )
            if not is_retryable_structured_result(
                finalized,
                schema_name=active_schema_name,
                context=active_context,
            ):
                _write_attempt_trace(
                    active_schema=active_schema,
                    active_context=active_context,
                    attempt_index=attempt_index,
                    result=finalized,
                    outcome="accepted",
                )
                return strip_structured_retry_metadata(finalized)
            result = finalized

        optional_empty = _optional_empty_result_for_schema(active_schema_name)
        if optional_empty is not None:
            _write_attempt_trace(
                active_schema=active_schema,
                active_context=active_context,
                attempt_index=attempt_index,
                result=optional_empty,
                outcome="fail_closed",
                fail_closed_reason="optional_empty_result",
            )
            return optional_empty

        if attempt_index >= len(schema_sequence) - 1:
            if _is_decision_schema(schema_name):
                _write_attempt_trace(
                    active_schema=active_schema,
                    active_context=active_context,
                    attempt_index=attempt_index,
                    result=result,
                    outcome="fail_closed",
                    fail_closed_reason="decision_fail_closed",
                )
                return build_decide_fail_closed_result(result)
            _write_attempt_trace(
                active_schema=active_schema,
                active_context=active_context,
                attempt_index=attempt_index,
                result=result,
                outcome="exhausted",
                fail_closed_reason="structured_retry_exhausted",
            )
            raise RuntimeError("LLM did not return structured output")
        active_context = add_retry_instruction_to_context(
            context=active_context,
            schema_name=active_schema_name,
            retry_nudge_style=profile.retry_nudge_style,
            schema=active_schema,
        )
        attempt_index += 1
        active_schema = schema_sequence[attempt_index]
        try:
            result = llm_api.call_structured(
                model=model,
                purpose=purpose,
                context=active_context,
                schema=active_schema,
            )
            _write_attempt_trace(
                active_schema=active_schema,
                active_context=active_context,
                attempt_index=attempt_index,
                result=result,
                outcome=(
                    "retryable"
                    if is_retryable_structured_result(
                        result,
                        schema_name=_schema_name(active_schema),
                        context=active_context,
                    )
                    else "accepted"
                ),
            )
        except Exception:
            if _is_decision_schema(schema_name):
                _write_attempt_trace(
                    active_schema=active_schema,
                    active_context=active_context,
                    attempt_index=attempt_index,
                    result=result,
                    outcome="fail_closed",
                    fail_closed_reason="retry_call_exception",
                )
                return build_decide_fail_closed_result(result)
            raise
