import json
import time
from typing import TYPE_CHECKING, Any

from openminion.modules.brain.adapters.llm.request import (
    _insert_retry_system_message,
    _messages_from_context,
)
from openminion.modules.brain.bootstrap.budget import trim_decide_context_to_budget
from openminion.modules.brain.bootstrap.context import _inject_decide_prompt_contract
from openminion.modules.brain.bootstrap.freshness_classify import (
    classify_request_freshness,
    freshness_from_entry_response,
)
from openminion.modules.brain.bootstrap.guards import _tier_0_restriction_decision
from openminion.modules.brain.bootstrap.recovery import (
    _act_seeded_decision,
    _emit_decide_fail_closed_event,
    _recover_simple_tool_parity_decision,
    _respond_decision,
    heuristic_decision,
)
from openminion.modules.brain.execution.delegation import _runner_delegate
from openminion.modules.brain.loop.tools.runtime import (
    DefaultAdaptiveToolLoopLLMRuntime,
    build_runtime_tool_specs,
)
from openminion.modules.brain.tools.parser import normalize_tool_name_for_brain
from openminion.modules.brain.constants import (
    BRAIN_ACT_PROFILE_CODING,
    BRAIN_DECISION_ROUTE_ACT,
)
from openminion.modules.brain.diagnostics.events import CanonicalEventLogger
from openminion.modules.brain.trailers import (
    EXPECTED_TRAILERS_METADATA_KEY,
    TrailerPostprocessService,
)
from openminion.modules.brain.bootstrap.route_catalog import decision_route_descriptions
from openminion.modules.brain.schemas import (
    ActDecision,
    Decision,
    RespondDecision,
    WorkingState,
    iso_now,
    new_uuid,
)
from openminion.modules.brain.retry import build_entry_retry_message
from .entry_routing import (
    _bypass_decision_for_route,
    _entry_coding_decision,
    _entry_decompose_decision,
    _entry_mutation_seed_should_route_to_coding,
    _entry_query_text,
    _entry_research_decision,
    _entry_user_file_artifact_should_route_to_coding,
    _is_empty_entry_response,
    _local_route,
    _provisional_entry_route,
    _response_usage_payload,
    _should_bypass_unified_entry,
)
from .entry import (
    build_entry_requestable_tool_specs,
    build_entry_tool_specs,
    detect_entry_path,
)
from .failures import _internal_failure_answer, _provider_failure_payload
from .providers.retry import (
    build_provider_retry_policy,
    classify_retryable,
    compute_backoff_ms,
)


_BACKOFF_SLEEP = time.sleep

if TYPE_CHECKING:  # pragma: no cover - typing only
    from openminion.modules.brain.runner import BrainRunner


def _apply_freshness_result(
    *,
    state: WorkingState,
    logger: CanonicalEventLogger,
    result: tuple[Any, Any, Any],
) -> None:
    contract, obligations, diagnostics = result
    state.freshness_contract = contract
    state.freshness_obligations = obligations
    state.freshness_diagnostics = diagnostics
    logger.emit(
        "brain.freshness.classified",
        {
            "domain": contract.domain.value,
            "time_sensitive": contract.time_sensitive,
            "needs_live_data": contract.needs_live_data,
            "needs_sources": contract.needs_sources,
            "needs_exact_date": contract.needs_exact_date,
            "answer_mode": contract.answer_mode.value,
            "classifier_mode": diagnostics.classifier_mode,
            "classifier_model": diagnostics.classifier_model,
        },
        trace_id=state.trace_id,
    )


def _compact_entry_tool_directory(tool_specs: list[Any]) -> str:
    lines = [
        "Execution tool schemas are inactive until requested. If execution is "
        "needed, call tool.request with one exact name from this directory."
    ]
    for spec in tool_specs:
        name = str(getattr(spec, "name", "") or "").strip()
        if not name:
            continue
        description = " ".join(str(getattr(spec, "description", "") or "").split())
        if len(description) > 120:
            description = f"{description[:117].rstrip()}..."
        lines.append(f"- {name}: {description or name}")
    return "\n".join(lines)


def _json_size(value: Any) -> int:
    return len(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    )


def _entry_request_metrics(
    *,
    messages: list[Any],
    tool_specs: list[Any],
    context_tokens: int,
) -> dict[str, int]:
    message_payload = [
        message.model_dump(mode="json") if hasattr(message, "model_dump") else message
        for message in messages
    ]
    tool_payload = [
        spec.model_dump(mode="json") if hasattr(spec, "model_dump") else spec
        for spec in tool_specs
    ]
    segment_ids = {
        str(segment_id)
        for message in messages
        for part in list(getattr(message, "content_parts", []) or [])
        for segment_id in list(getattr(part, "segment_ids", []) or [])
        if str(segment_id).strip()
    }
    return {
        "request_bytes": _json_size(
            {"messages": message_payload, "tools": tool_payload}
        ),
        "context_bytes": _json_size(message_payload),
        "context_tokens": max(0, int(context_tokens or 0)),
        "context_segment_count": len(segment_ids),
        "tool_schema_count": len(tool_specs),
        "tool_schema_bytes": _json_size(tool_payload),
        "exposed_tool_count": len(tool_specs),
    }


def _entry_response_bytes(response: Any) -> int:
    return _json_size(
        {
            "output_text": str(getattr(response, "output_text", "") or ""),
            "tool_calls": [
                call.model_dump(mode="json") if hasattr(call, "model_dump") else call
                for call in list(getattr(response, "tool_calls", []) or [])
            ],
            "finish_reason": str(getattr(response, "finish_reason", "") or ""),
        }
    )


def _entry_response_only_requests_tool_directory(response: Any) -> bool:
    tool_calls = list(getattr(response, "tool_calls", []) or [])
    if not tool_calls:
        return False
    return all(
        str(getattr(call, "name", "") or "").strip() == "tool.request"
        for call in tool_calls
    )


def _apply_entry_freshness(
    *,
    runner: Any,
    state: WorkingState,
    logger: CanonicalEventLogger,
    response: Any,
    user_input: str | None,
    model: str,
    llm_call_id: str,
) -> None:
    result = freshness_from_entry_response(
        response,
        user_input=str(user_input or ""),
        model=model,
    )
    if result is not None:
        _apply_freshness_result(state=state, logger=logger, result=result)
        return

    fallback_call_id = new_uuid()
    result = classify_request_freshness(
        runner,
        state=state,
        user_input=user_input,
        logger=logger,
    )
    result[2].notes.append(
        "Compatibility fallback was required because the entry response did not "
        "contain a valid typed freshness contract."
    )
    logger.emit(
        "brain.freshness.entry_fallback",
        {"llm_call_id": llm_call_id, "reason": "missing_or_invalid_contract"},
        trace_id=state.trace_id,
    )
    if callable(getattr(runner.llm_api, "call_structured", None)) and result[
        2
    ].classifier_mode not in {
        "skipped_direct_tool_request",
        "skipped_empty_request",
        "skipped_missing_llm",
    }:
        fallback_payload = {
            "llm_call_id": fallback_call_id,
            "purpose": "freshness_classify_fallback",
            "model": model,
            "auxiliary": True,
        }
        logger.emit("llm.call.started", fallback_payload, trace_id=state.trace_id)
        logger.emit(
            "llm.call.completed",
            {
                **fallback_payload,
                "usage": {},
                "fallback_reason": "missing_or_invalid_entry_contract",
            },
            trace_id=state.trace_id,
        )
    _apply_freshness_result(state=state, logger=logger, result=result)


def _with_forced_runtime_tool_specs(
    runner: Any,
    tool_specs: list[Any],
    forced_tools: list[str] | None,
) -> list[Any]:
    forced = {str(name or "").strip() for name in list(forced_tools or [])}
    forced.discard("")
    if not forced:
        return tool_specs
    existing = {
        str(getattr(spec, "name", "") or "").strip()
        for spec in tool_specs
        if str(getattr(spec, "name", "") or "").strip()
    }
    missing = forced - existing
    if not missing:
        return tool_specs
    dynamic_specs = build_runtime_tool_specs(runner, allowed_tools=frozenset(missing))
    return [*tool_specs, *dynamic_specs]


def _explicit_direct_tool_names_from_user_input(user_input: str | None) -> list[str]:
    text = str(user_input or "").strip()
    if not text.lower().startswith("tool "):
        return []
    parts = text.split(maxsplit=2)
    if len(parts) < 2:
        return []
    raw_name = str(parts[1] or "").strip()
    if not raw_name:
        return []
    return [normalize_tool_name_for_brain(raw_name) or raw_name]


_IDLE_TICK_ALLOWED_TOOLS: frozenset[str] = frozenset({"plan"})
_PAE_IDLE_TICK_NOOP_REASON_CODE = "pae_idle_tick_noop"
_PAE_IDLE_TICK_NOOP_ANSWER_SENTINEL = "[pae:no_op]"


def _enforce_idle_tick_v1_bound(
    *,
    detection: Any,
    logger: CanonicalEventLogger,
    trace_id: str | None,
    llm_call_id: str,
) -> Decision | None:
    """Enforce the v1 idle-tick action bound and coerce unsupported actions."""
    tool_call_names = [
        str(name or "").strip()
        for name in getattr(detection, "tool_call_names", ()) or ()
        if str(name or "").strip()
    ]
    non_plan = [
        name for name in tool_call_names if name not in _IDLE_TICK_ALLOWED_TOOLS
    ]
    response_text = str(getattr(detection, "response_text", "") or "").strip()
    path = str(getattr(detection, "path", "") or "").strip()

    if path == "respond" and not response_text and not non_plan:
        return _respond_decision(
            confidence=1.0,
            reason_code=_PAE_IDLE_TICK_NOOP_REASON_CODE,
            answer=_PAE_IDLE_TICK_NOOP_ANSWER_SENTINEL,
        )

    reason: str | None = None
    detail: dict[str, Any] = {}
    if non_plan:
        reason = "non_plan_tool_call"
        detail = {
            "actions": non_plan,
            "allowed": sorted(_IDLE_TICK_ALLOWED_TOOLS),
        }
    elif path == "clarify":
        reason = "clarify_during_idle_tick"
        detail = {
            "clarify_question": str(
                getattr(detection, "clarify_question", "") or ""
            ).strip(),
        }
    elif path == "respond" and response_text:
        reason = "non_empty_respond_during_idle_tick"
        detail = {
            "response_preview": response_text[:200],
            "response_chars": len(response_text),
        }

    if reason is None:
        return None

    logger.emit(
        "pae.unsupported_v1_action",
        {
            "reason": reason,
            "llm_call_id": llm_call_id,
            **detail,
        },
        trace_id=trace_id,
        status="warning",
    )
    return _respond_decision(
        confidence=1.0,
        reason_code=_PAE_IDLE_TICK_NOOP_REASON_CODE,
        answer=_PAE_IDLE_TICK_NOOP_ANSWER_SENTINEL,
    )


def _emit_decide_pre_call_status(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    estimate: int,
    tool_specs: list[Any],
) -> None:
    estimated_tool_tokens = sum(
        max(1, len(str(getattr(spec, "input_schema", "") or "")) // 4)
        + max(1, len(str(getattr(spec, "description", "") or "")) // 4)
        for spec in tool_specs
    )
    _estimated_outbound = estimate + estimated_tool_tokens
    _pre_call_emit = getattr(runner, "_emit_phase_status", None)
    if callable(_pre_call_emit):
        _pre_call_payload: dict[str, Any] = {"turn.llm_call_count": 1, "turn.llm_call_limit": 1}
        if _estimated_outbound > 0:
            _pre_call_payload.update(
                {
                    "total_input_tokens_used": _estimated_outbound,
                    "total_tokens_used": _estimated_outbound,
                    "token_usage_estimated": True,
                }
            )
        _pre_call_emit(state=state, source_phase="DECIDE", payload=_pre_call_payload)


def decide(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    user_input: str | None,
    logger: CanonicalEventLogger,
    forced_tools: list[str] | None = None,
    capability_category: str | None = None,
) -> Decision:
    has_new_input = bool(user_input and str(user_input).strip())
    normalized_capability_category = (
        str(capability_category or "").strip().lower() or None
    )
    is_idle_tick_entry = str(getattr(state, "run_trigger", "") or "") == "idle_tick"
    if (
        state.plan is not None
        and state.cursor < len(state.plan.steps)
        and not has_new_input
        and not is_idle_tick_entry
    ):
        return _act_seeded_decision(
            confidence=1.0,
            reason_code="resume_existing_plan",
            command=state.plan.steps[state.cursor],
            rationale=str(getattr(state, "decision_rationale", "") or "").strip(),
            sub_intents=list(getattr(state, "decision_sub_intents", []) or []),
        )

    skill_hints: dict[str, Any] = {}
    if has_new_input and user_input is not None:
        skill_hints = _runner_delegate(
            "_resolve_skill_hints",
            runner,
            intent=user_input,
            purpose="plan",
            state=state,
            logger=logger,
        )

    if runner.llm_api is None or runner.context_api is None:
        if user_input is not None:
            _apply_freshness_result(
                state=state,
                logger=logger,
                result=classify_request_freshness(
                    runner,
                    state=state,
                    user_input=user_input,
                    logger=logger,
                ),
            )
        decision = heuristic_decision(runner, state=state, user_input=user_input)
        if state.tier == "T0_direct" and decision.route in {BRAIN_DECISION_ROUTE_ACT}:
            return _tier_0_restriction_decision(
                logger=logger,
                state=state,
                blocked_mode=decision.route,
            )
        return decision

    budget_stop = _runner_delegate("_consume_tick", runner, state)
    if budget_stop is not None:
        return _respond_decision(
            confidence=1.0,
            reason_code=str(getattr(budget_stop, "value", budget_stop) or "").strip()
            or "tick_budget_exhausted",
            answer=_internal_failure_answer(
                detail=str(getattr(budget_stop, "value", budget_stop) or "").strip()
                or "tick_budget_exhausted"
            ),
        )

    if state.llm_calls_used >= state.llm_calls_max:
        return _respond_decision(
            confidence=1.0,
            reason_code="llm_call_budget_exceeded",
            answer=_internal_failure_answer(detail="llm_call_budget_exceeded"),
        )

    provisional_route = _provisional_entry_route(
        runner=runner,
        state=state,
        has_new_user_input=has_new_input,
    )
    if _should_bypass_unified_entry(route=provisional_route, state=state):
        if state.tier == "T0_direct":
            return _tier_0_restriction_decision(
                logger=logger,
                state=state,
                blocked_mode=BRAIN_DECISION_ROUTE_ACT,
            )
        return _bypass_decision_for_route(provisional_route)

    model = (
        str(getattr(runner.profile.llm_profiles, "act_model", "") or "").strip()
        or str(getattr(runner.profile.llm_profiles, "decide_model", "") or "").strip()
    )
    llm_call_id = new_uuid()
    query_text = _entry_query_text(state=state, user_input=user_input)
    tool_specs, supports_seed_response = build_entry_tool_specs(
        runner,
        act_profile=str(getattr(provisional_route, "act_profile", "") or ""),
        execution_target_kind=str(
            getattr(getattr(provisional_route, "execution_target", None), "kind", "")
            or ""
        ),
        include_control_tools=(
            str(getattr(state, "decision_reason_code", "") or "").strip()
            != "research_iteration_fallback"
        ),
    )
    requestable_tool_specs = build_entry_requestable_tool_specs(
        runner,
        act_profile=str(getattr(provisional_route, "act_profile", "") or ""),
        execution_target_kind=str(
            getattr(getattr(provisional_route, "execution_target", None), "kind", "")
            or ""
        ),
    )
    explicit_runtime_tools = [
        *list(forced_tools or []),
        *_explicit_direct_tool_names_from_user_input(user_input),
    ]
    tool_specs = _with_forced_runtime_tool_specs(
        runner,
        tool_specs,
        explicit_runtime_tools,
    )
    if forced_tools:
        forced = {str(name).strip() for name in forced_tools if str(name).strip()}
        tool_specs = [
            spec for spec in tool_specs if spec.name == "clarify" or spec.name in forced
        ]
    if normalized_capability_category:
        available_tool_names = {
            str(spec.name or "").strip()
            for spec in tool_specs
            if str(spec.name or "").strip()
            and str(spec.name or "").strip() != "clarify"
        }
        preferred_tool = _runner_delegate(
            "_resolve_capability_tool_fallback",
            runner,
            category=normalized_capability_category,
            available_tools=available_tool_names,
        )
        if preferred_tool:
            tool_specs = [
                spec
                for spec in tool_specs
                if spec.name == "clarify" or spec.name == preferred_tool
            ]
    if state.tier == "T0_direct":
        tool_specs = [spec for spec in tool_specs if spec.name == "clarify"]
    if str(getattr(state, "run_trigger", "") or "") == "idle_tick":
        tool_specs = [
            spec
            for spec in tool_specs
            if str(getattr(spec, "name", "") or "").strip() == "plan"
        ]
    runtime_tool_names = {
        str(spec.name or "").strip()
        for spec in tool_specs
        if str(spec.name or "").strip()
    }
    runtime_tool_schemas = [
        item
        for item in _runner_delegate("_collect_runtime_tool_schemas", runner)
        if str(item.get("name", "") or "").strip() in runtime_tool_names
    ]
    hints: dict[str, Any] = {
        "user_input": query_text,
        "_llm_call_id": llm_call_id,
        "current_datetime": iso_now(),
        "prompt_tool_schemas_enabled": bool(
            getattr(runner, "_prompt_tool_schemas_enabled", False)
        ),
        "decision_route_descriptions": decision_route_descriptions(runner.profile),
        "entry_bootstrap_act_profile": str(
            getattr(provisional_route, "act_profile", "") or ""
        ).strip()
        or None,
        "entry_bootstrap_execution_target": str(
            getattr(getattr(provisional_route, "execution_target", None), "kind", "")
            or ""
        ).strip()
        or None,
        "entry_supports_seed_response": supports_seed_response,
        "entry_clarify_available": True,
    }
    if runtime_tool_schemas:
        hints["runtime_tool_schemas"] = runtime_tool_schemas
    else:
        hints["think_steps_available"] = True
    if state.tier == "T0_direct":
        hints["entry_tool_restriction"] = "clarify_only"
    if state.step_outputs:
        hints["has_prior_results"] = True
    if requestable_tool_specs and any(
        spec.name == "tool.request" for spec in tool_specs
    ):
        style_overrides = hints.setdefault("style_overrides", {})
        if isinstance(style_overrides, dict):
            style_overrides["entry_inactive_tool_directory"] = (
                _compact_entry_tool_directory(requestable_tool_specs)
            )
    if normalized_capability_category:
        hints["capability_category"] = normalized_capability_category
    if str(getattr(state, "run_trigger", "") or "") == "idle_tick":
        hints["idle_tick_entry"] = True
        hints["idle_tick_v1_actions"] = ["continue_plan", "no_op"]
    hints.update(skill_hints)
    _inject_decide_prompt_contract(hints, runner=runner)

    logger.emit(
        "llm.call.started",
        {"llm_call_id": llm_call_id, "purpose": "entry", "model": model},
        trace_id=state.trace_id,
    )
    _runner_delegate("_track_call_started", runner, llm_call_id, "entry", model)

    budget_max_tokens = min(2000, state.budgets_remaining.tokens)
    context = _runner_delegate(
        "_build_context",
        runner,
        state=state,
        purpose="decide",
        budget={"max_tokens": budget_max_tokens},
        hints=hints,
        logger=logger,
    )
    estimate = _runner_delegate(
        "_estimate_tokens", runner, model=model, context=context
    )
    context, _estimate, budget_decision = trim_decide_context_to_budget(
        runner=runner,
        state=state,
        logger=logger,
        model=model,
        budget_max_tokens=budget_max_tokens,
        hints=hints,
        context=context,
        estimate=estimate,
        user_input=user_input,
    )
    if budget_decision is not None:
        return budget_decision

    try:
        runtime = DefaultAdaptiveToolLoopLLMRuntime.from_adapter(runner.llm_api)
    except Exception as exc:  # noqa: BLE001
        _emit_decide_fail_closed_event(
            logger=logger,
            state=state,
            reason_code="entry_runtime_unavailable",
            source="entry_runtime_unavailable",
            metadata={"llm_call_id": llm_call_id, "error": str(exc)},
        )
        return _respond_decision(
            confidence=0.3,
            reason_code="entry_runtime_unavailable",
            answer=_internal_failure_answer(detail="entry_runtime_unavailable"),
        )

    response = None
    retry_policy = build_provider_retry_policy(getattr(runner, "config", None))
    max_retries = retry_policy.max_retries
    last_detection = None
    base_messages = list(_messages_from_context(context))
    request_metrics = _entry_request_metrics(
        messages=base_messages,
        tool_specs=tool_specs,
        context_tokens=int(_estimate or 0),
    )
    has_real_tools = any(spec.name != "clarify" for spec in tool_specs)
    entry_metadata: dict[str, Any] = {
        "purpose": "entry",
        "mode_name": "act",
        "agent_id": state.agent_id,
        "trace_id": state.trace_id,
        "capability_category": normalized_capability_category,
    }
    expected_trailers = hints.get(EXPECTED_TRAILERS_METADATA_KEY)
    if isinstance(expected_trailers, list | tuple) and expected_trailers:
        entry_metadata[EXPECTED_TRAILERS_METADATA_KEY] = [
            str(item or "").strip()
            for item in expected_trailers
            if str(item or "").strip()
        ]

    _emit_decide_pre_call_status(
        runner,
        state=state,
        estimate=int(_estimate or 0),
        tool_specs=tool_specs,
    )

    for attempt in range(max_retries + 1):
        logger.emit(
            "llm.identity_audit",
            {
                "llm_call_id": llm_call_id,
                "purpose": "entry",
                "agent_id": state.agent_id,
                "profile_version": getattr(
                    runner.profile, "profile_version", "unknown"
                ),
                "trace_id": state.trace_id,
            },
            trace_id=state.trace_id,
        )
        messages = list(base_messages)
        if attempt:
            messages = _insert_retry_system_message(
                messages,
                retry_message=build_entry_retry_message(has_real_tools=has_real_tools),
            )
        try:
            response = runtime.complete(
                messages=messages,
                tools=tool_specs,
                model=model,
                tool_choice="auto",
                max_output_tokens=budget_max_tokens,
                metadata=entry_metadata,
            )
        except Exception as exc:  # noqa: BLE001
            error_category, retryable = classify_retryable(exc)
            if retryable and attempt < max_retries:
                backoff_ms = compute_backoff_ms(retry_policy, attempt)
                logger.emit(
                    "llm.call.retry",
                    {
                        "llm_call_id": llm_call_id,
                        "attempt": attempt + 1,
                        "reason": "provider_transient_error",
                        "error_category": error_category,
                        "backoff_ms": int(backoff_ms),
                    },
                    trace_id=state.trace_id,
                )
                _BACKOFF_SLEEP(backoff_ms / 1000.0)
                continue
            logger.emit(
                "llm.call.failed",
                {
                    "llm_call_id": llm_call_id,
                    "purpose": "entry",
                    "model": model,
                    "error": str(exc),
                    "error_category": error_category,
                    "attempts": attempt + 1,
                },
                trace_id=state.trace_id,
                status="error",
                error={"code": "LLM_CALL_FAILED", "message": str(exc)},
            )
            provider_payload = _provider_failure_payload(exc, confidence=0.5)
            if provider_payload is not None:
                return RespondDecision.model_validate(provider_payload)
            raise
        last_detection = detect_entry_path(response)
        if not _is_empty_entry_response(response):
            break
        if (
            str(getattr(state, "run_trigger", "") or "") == "idle_tick"
            and last_detection is not None
            and last_detection.path == "respond"
        ):
            logger.emit(
                "llm.call.empty_response_accepted",
                {
                    "llm_call_id": llm_call_id,
                    "reason": "idle_tick_noop",
                },
                trace_id=state.trace_id,
            )
            break
        if attempt < max_retries:
            logger.emit(
                "llm.call.retry",
                {
                    "llm_call_id": llm_call_id,
                    "attempt": attempt + 1,
                    "reason": "empty_entry_response",
                },
                trace_id=state.trace_id,
            )
            continue
        logger.emit(
            "llm.call.failed",
            {
                "llm_call_id": llm_call_id,
                "purpose": "entry",
                "model": model,
                "error": "exhausted_retries_empty_response",
            },
            trace_id=state.trace_id,
            status="error",
            error={
                "code": "LLM_EMPTY_RESPONSE_EXHAUSTED",
                "message": "Provider returned empty entry responses after retries",
            },
        )
        _emit_decide_fail_closed_event(
            logger=logger,
            state=state,
            reason_code="llm_empty_response",
            source="entry_empty_response_retry_exhausted",
            metadata={"llm_call_id": llm_call_id, "attempts": max_retries + 1},
        )
        return _respond_decision(
            confidence=0.5,
            reason_code="llm_empty_response",
            answer=_internal_failure_answer(detail="llm_empty_response"),
        )

    if response is None or last_detection is None:
        return _respond_decision(
            confidence=0.5,
            reason_code="entry_missing_response",
            answer=_internal_failure_answer(detail="entry_missing_response"),
        )

    _apply_entry_freshness(
        runner=runner,
        state=state,
        logger=logger,
        response=response,
        user_input=user_input,
        model=model,
        llm_call_id=llm_call_id,
    )

    usage_payload = _response_usage_payload(response)
    state.llm_calls_used += 1
    _runner_delegate("_debit_tokens", runner, state, {"usage": usage_payload}, logger)
    _runner_delegate("_track_call_completed", runner, llm_call_id)

    _trailer_session_api = getattr(runner, "session_api", None)
    if _trailer_session_api is not None:
        _trailer_service = TrailerPostprocessService()
        _trailer_service.process(
            response=response,
            session_api=_trailer_session_api,
            session_id=state.session_id,
            agent_id=state.agent_id,
            trace_id=str(getattr(state, "trace_id", "") or ""),
            route="direct_respond"
            if last_detection.path == "respond"
            else (
                "direct_clarify" if last_detection.path == "clarify" else "entry_act"
            ),
            request_metadata=entry_metadata,
        )

    _emit_entry_token_status = getattr(runner, "_emit_phase_status", None)
    if callable(_emit_entry_token_status):
        _emit_entry_token_status(
            state=state,
            source_phase="DECIDE",
            payload={
                "total_input_tokens_used": int(
                    usage_payload.get("input_tokens", 0) or 0
                ),
                "total_output_tokens_used": int(
                    usage_payload.get("output_tokens", 0) or 0
                ),
                "total_tokens_used": int(usage_payload.get("total_tokens", 0) or 0),
                "turn.llm_call_count": 1,
                "turn.llm_call_limit": 1,
            },
        )

    logger.emit(
        "llm.call.completed",
        {
            "llm_call_id": llm_call_id,
            "purpose": "entry",
            "provider": str(getattr(response, "provider", "") or "").strip(),
            "model": model,
            "prompt_context_id": context.get("prompt_context_id"),
            "usage": usage_payload,
            "entry_tool_spec_count": len(tool_specs),
            **request_metrics,
            "response_bytes": _entry_response_bytes(response),
            "retry_count": attempt,
            "finish_reason": str(getattr(response, "finish_reason", "") or ""),
            "configured_output_limit": budget_max_tokens,
        },
        trace_id=state.trace_id,
    )
    logger.emit(
        "brain.entry.path_detected",
        {
            "path": last_detection.path,
            "tool_call_names": list(last_detection.tool_call_names),
            "bootstrap_act_profile": str(
                getattr(provisional_route, "act_profile", "") or ""
            ).strip()
            or None,
            "bootstrap_execution_target_kind": str(
                getattr(
                    getattr(provisional_route, "execution_target", None), "kind", ""
                )
                or ""
            ).strip()
            or None,
        },
        trace_id=state.trace_id,
    )

    if str(getattr(state, "run_trigger", "") or "") == "idle_tick":
        coerced = _enforce_idle_tick_v1_bound(
            detection=last_detection,
            logger=logger,
            trace_id=state.trace_id,
            llm_call_id=llm_call_id,
        )
        if coerced is not None:
            return coerced

    if last_detection.path == "clarify":
        decision = RespondDecision(
            confidence=0.5,
            reason_code="entry_clarify",
            respond_kind="clarify",
            question=last_detection.clarify_question,
        )
        return decision

    if last_detection.path == "respond":
        decision = _respond_decision(
            confidence=0.5,
            reason_code="entry_text_response",
            answer=last_detection.response_text,
        )
        recovered_decision = _recover_simple_tool_parity_decision(
            runner=runner,
            state=state,
            user_input=user_input,
            capability_category=normalized_capability_category,
            decision=decision,
            response=response,
            logger=logger,
            llm_call_id=llm_call_id,
        )
        if recovered_decision is not None:
            return recovered_decision
        return decision

    if state.tier == "T0_direct":
        return _tier_0_restriction_decision(
            logger=logger,
            state=state,
            blocked_mode=BRAIN_DECISION_ROUTE_ACT,
        )
    research_decision = _entry_research_decision(
        response=response,
        logger=logger,
        state=state,
        llm_call_id=llm_call_id,
        respond_decision_fn=_respond_decision,
    )
    if research_decision is not None:
        return research_decision
    coding_decision = _entry_coding_decision(
        response=response,
        logger=logger,
        state=state,
        llm_call_id=llm_call_id,
        respond_decision_fn=_respond_decision,
    )
    if coding_decision is not None:
        return coding_decision
    decompose_decision = _entry_decompose_decision(
        response=response,
        provisional_route=provisional_route,
        logger=logger,
        state=state,
        llm_call_id=llm_call_id,
        respond_decision_fn=_respond_decision,
    )
    if decompose_decision is not None:
        return decompose_decision
    decision = ActDecision(
        confidence=0.5,
        reason_code="entry_tool_call",
    )
    decision._entry_response = response
    if _entry_mutation_seed_should_route_to_coding(
        response=response,
        provisional_route=provisional_route,
    ):
        decision.reason_code = "entry_coding_seed_tool_call"
        decision.act_profile = BRAIN_ACT_PROFILE_CODING
        decision._pre_resolved_act_route = _local_route(
            act_profile=BRAIN_ACT_PROFILE_CODING,
            source="entry_mutation_seed_tool_call",
        )
        return decision
    if _entry_user_file_artifact_should_route_to_coding(
        state=state,
        user_input=user_input,
        provisional_route=provisional_route,
    ):
        decision.reason_code = "entry_coding_user_file_artifact_request"
        decision.act_profile = BRAIN_ACT_PROFILE_CODING
        if _entry_response_only_requests_tool_directory(response):
            decision._entry_response = None
        decision._pre_resolved_act_route = _local_route(
            act_profile=BRAIN_ACT_PROFILE_CODING,
            source="entry_user_file_artifact_request",
        )
        return decision
    decision._pre_resolved_act_route = provisional_route
    return decision
