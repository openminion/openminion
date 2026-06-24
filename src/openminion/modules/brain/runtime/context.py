"""Build runtime context payloads for the brain."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from openminion.modules.brain.runtime.reasoning import (
    ThinkingCtl,
    ThinkingRequest,
    ThinkingResolutionInput,
)

from ..diagnostics.events import CanonicalEventLogger
from ..bootstrap.route_catalog import get_route_descriptor
from ..schemas import BudgetTelemetryBlock, BudgetTelemetryConfig, LearningLoopMetric
from ..schemas import WorkingState, iso_now
from ..meta.schemas import LowProgressSignal, MetaConfig

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..runner import BrainRunner

from openminion.modules.context.config import (
    CONTEXT_POST_COMPLETION_CRITIQUE_LIMIT,
    CONTEXT_STRATEGY_OUTCOME_LIMIT,
)
from openminion.modules.brain.constants import STATE_KEY_MODULE_STATE


_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

_COMMON_HINT_KEYS = {
    "_llm_call_id",
    "budget_telemetry",
    "continuation_guidance",
    "current_datetime",
    "freshness_contract",
    "freshness_obligations",
    "freshness_diagnostics",
    "user_input",
    "mode_name",
    "context_budget_tier",
    "live_state_overlay",
    "pending_clarifications",
    "pending_conversational_clarification",
    "clarification_responses",
    "gateway_system_context",
    "model_capability_overrides",
}
_THINKING_CTL = ThinkingCtl()

_PHASE_HINT_KEYS: dict[str, set[str]] = {
    "decide": {
        "decision_route_descriptions",
        "available_delegate_targets",
        "forced_tools",
        "capability_category",
        "prompt_tool_schemas_enabled",
        "runtime_tool_schemas",
        "tool_aware",
        "has_prior_results",
        "think_steps_available",
        "introspection_intent",
        "style_overrides",
        # idle-tick decide hints. `idle_tick_entry` is a bool
        "idle_tick_entry",
        "idle_tick_v1_actions",
    },
    "plan": {
        "prompt_tool_schemas_enabled",
        "runtime_tool_schemas",
        "think_steps_available",
        "step_history",
        "plan_sub_intents",
        "completed_intent_states",
        "remaining_intent_states",
        "blocked_intent_states",
        "adaptive_revision_context",
        "style_overrides",
    },
    "act": {
        "output_key",
        "prior_step_result",
        "step_history",
        "style_overrides",
    },
    "judge": {
        "closure_candidate_reason",
        "closure_action_summary",
        "closure_action_outputs",
        "closure_sub_intents",
        "closure_intent_outcomes",
        "closure_success_criteria",
        "post_action_fact_kind",
        "post_action_runtime_facts",
        "post_action_intent_outcomes",
        "post_action_success_criteria",
        "style_overrides",
    },
    "reflect": {
        "command",
        "result",
        "reflection_context_kind",
        "reflection_goal_summary",
        "reflection_plan_objective",
        "reflection_plan_progress",
        "reflection_step_context",
        "reflection_prior_outcomes",
        "reflection_full_step_history",
        "reflection_success_criteria",
        "style_overrides",
    },
    "validate": {
        "runtime_tool_schemas",
        "prompt_tool_schemas_enabled",
        "feasibility_sub_intents",
        "feasibility_plan_steps",
        "feasibility_runtime_facts",
        "style_overrides",
    },
}


def _is_phase_hint_allowed(*, purpose: str, key: str) -> bool:
    if key in _COMMON_HINT_KEYS:
        return True
    if key.startswith("skill_"):
        return True
    if key.startswith("thinking_"):
        return True
    allowed = _PHASE_HINT_KEYS.get(purpose, set())
    return key in allowed


def _thinking_model_for_purpose(*, runner: "BrainRunner", purpose: str) -> str:
    profile = getattr(runner, "profile", None)
    llm_profiles = getattr(profile, "llm_profiles", None)
    if llm_profiles is None:
        return ""
    if purpose in {"act", "validate"}:
        return str(getattr(llm_profiles, "act_model", "") or "").strip()
    if purpose == "plan":
        return str(getattr(llm_profiles, "plan_model", "") or "").strip()
    if purpose == "summarize":
        return str(getattr(llm_profiles, "summarize_model", "") or "").strip()
    return str(getattr(llm_profiles, "reflect_model", "") or "").strip()


def _thinking_provider_name(*, runner: "BrainRunner") -> str:
    llm_api = getattr(runner, "llm_api", None)
    client = getattr(llm_api, "client", None)
    for candidate in (llm_api, client):
        value = str(getattr(candidate, "name", "") or "").strip().lower()
        if value:
            return value
    return ""


def _budget_telemetry_config(runner: "BrainRunner") -> BudgetTelemetryConfig:
    raw = getattr(getattr(runner, "profile", None), "budget_telemetry", None)
    if raw is None:
        return BudgetTelemetryConfig()
    if isinstance(raw, BudgetTelemetryConfig):
        return raw
    return BudgetTelemetryConfig.model_validate(raw)


def _budget_envelope_status_from_fractions(fractions: list[float]) -> str:
    if not fractions:
        return "comfortable"
    remaining_fraction = min(max(0.0, min(1.0, value)) for value in fractions)
    if remaining_fraction <= 0.2:
        return "near_exhaustion"
    if remaining_fraction <= 0.5:
        return "tight"
    return "comfortable"


def _budget_telemetry_overlay(
    runner: "BrainRunner",
    *,
    state: WorkingState,
) -> dict[str, Any]:
    config = _budget_telemetry_config(runner)
    if not config.enabled:
        return {}

    budgets_remaining = getattr(state, "budgets_remaining", None)
    if budgets_remaining is None:
        return {}

    mission_budget = getattr(getattr(state, "mission", None), "budget", None)
    turn_budget = getattr(mission_budget, "turn_budget_allocated", None) or getattr(
        mission_budget, "turn_budget_baseline", None
    )
    profile_budgets = getattr(getattr(runner, "profile", None), "budgets", None)

    def _fallback_max(field: str, remaining: int, profile_attr: str) -> int:
        candidate = int(getattr(turn_budget, field, 0) or 0)
        if candidate > 0:
            return candidate
        candidate = int(getattr(profile_budgets, profile_attr, 0) or 0)
        if candidate > 0:
            return candidate
        return max(0, int(remaining or 0))

    iteration_remaining = int(getattr(budgets_remaining, "ticks", 0) or 0)
    iteration_max = _fallback_max(
        "ticks", iteration_remaining, "max_ticks_per_user_turn"
    )
    iteration_used = max(0, iteration_max - iteration_remaining)

    tool_calls_remaining = int(getattr(budgets_remaining, "tool_calls", 0) or 0)
    tool_calls_max = _fallback_max("tool_calls", tool_calls_remaining, "max_tool_calls")
    tool_calls_used = max(0, tool_calls_max - tool_calls_remaining)

    token_remaining = int(getattr(budgets_remaining, "tokens", 0) or 0)
    token_max = _fallback_max("tokens", token_remaining, "max_total_llm_tokens")
    token_used = max(0, token_max - token_remaining)

    time_remaining_ms = int(getattr(budgets_remaining, "time_ms", 0) or 0)
    time_max_ms = _fallback_max("time_ms", time_remaining_ms, "max_elapsed_ms")
    time_elapsed_ms = max(0, time_max_ms - time_remaining_ms)

    fractions = []
    for remaining, maximum in (
        (iteration_remaining, iteration_max),
        (tool_calls_remaining, tool_calls_max),
    ):
        if maximum > 0:
            fractions.append(float(remaining) / float(maximum))
    if config.granularity == "fine":
        if token_max > 0:
            fractions.append(float(token_remaining) / float(token_max))
        if time_max_ms > 0:
            fractions.append(float(time_remaining_ms) / float(time_max_ms))

    block = BudgetTelemetryBlock(
        iteration_used=iteration_used,
        iteration_remaining=iteration_remaining,
        iteration_max=iteration_max,
        tool_calls_used=tool_calls_used,
        tool_calls_remaining=tool_calls_remaining,
        tool_calls_max=tool_calls_max,
        token_used=token_used if config.granularity == "fine" else None,
        token_remaining=token_remaining if config.granularity == "fine" else None,
        token_max=token_max if config.granularity == "fine" else None,
        time_elapsed_ms=time_elapsed_ms if config.granularity == "fine" else None,
        time_remaining_ms=(time_remaining_ms if config.granularity == "fine" else None),
        budget_envelope_status=_budget_envelope_status_from_fractions(fractions),
    )
    return {"budget_telemetry": block.model_dump(mode="json", exclude_none=True)}


def _build_thinking_hints(
    runner: "BrainRunner",
    *,
    purpose: str,
    hints: dict[str, Any],
) -> dict[str, Any]:
    profile = getattr(runner, "profile", None)
    agent_profile = str(getattr(profile, "thinking", "") or "").strip() or None
    mode_name = str(hints.get("mode_name", "") or "").strip() or None
    mode_spec = get_route_descriptor(mode_name or "") if mode_name else None
    request_profile = (
        str(hints.get("thinking_requested_profile", "") or "").strip() or None
    )
    resolved = _THINKING_CTL.resolve_mode_aware(
        request=ThinkingRequest(
            purpose=purpose,
            requested_profile=request_profile,
            provider=_thinking_provider_name(runner=runner) or None,
            model=_thinking_model_for_purpose(runner=runner, purpose=purpose) or None,
            metadata={"context_owner": "brain.context"},
        ),
        layers=ThinkingResolutionInput(
            code_default_profile="minimal",
            agent_profile=agent_profile,
            request_profile=request_profile,
        ),
        mode_policy=getattr(mode_spec, "thinking_policy", None),
        mode_name=mode_name,
    )
    return _THINKING_CTL.build_context_hints(resolved=resolved)


def _dedupe_text_values(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        deduped.append(text)
    return deduped


def _slugify_structural_value(value: Any, *, default: str = "") -> str:
    normalized = _NON_ALNUM_RE.sub("-", str(value or "").strip().lower()).strip("-")
    if normalized:
        return normalized[:48]
    return default


def _command_tool_names(command: Any) -> tuple[str, ...]:
    if str(getattr(command, "kind", "") or "").strip().lower() != "tool":
        return ()
    tool_name = str(getattr(command, "tool_name", "") or "").strip()
    return (tool_name,) if tool_name else ()


def _plan_cursor_tool_names(state: WorkingState) -> tuple[str, ...]:
    plan = getattr(state, "plan", None)
    if plan is None:
        return ()
    steps = list(getattr(plan, "steps", []) or [])
    cursor = int(getattr(state, "cursor", 0) or 0)
    if cursor < 0 or cursor >= len(steps):
        return ()
    return _command_tool_names(steps[cursor])


def _state_tool_names(state: WorkingState) -> tuple[str, ...]:
    names: list[str] = []
    command = getattr(state, "pending_confirmation_command", None)
    names.extend(_command_tool_names(command))
    if not names:
        names.extend(_plan_cursor_tool_names(state))
    seen: set[str] = set()
    ordered: list[str] = []
    for name in names:
        normalized = str(name or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return tuple(ordered)


def _state_error_slugs(state: WorkingState) -> tuple[str, ...]:
    error = getattr(getattr(state, "last_result", None), "error", None)
    values = [
        getattr(error, "code", ""),
        getattr(error, "message", ""),
        getattr(state, "failure_type", ""),
    ]
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        slug = _slugify_structural_value(value)
        if not slug or slug in seen:
            continue
        seen.add(slug)
        ordered.append(slug)
    return tuple(ordered)


def _improvement_note_overlay(
    runner: "BrainRunner",
    *,
    state: WorkingState,
) -> dict[str, Any]:
    engine = getattr(runner, "_self_improvement_engine", None)
    if engine is None or not getattr(engine, "enabled", False):
        return {}
    tool_names = _state_tool_names(state)
    if not tool_names:
        return {}
    error_slugs = _state_error_slugs(state)
    notes = engine.find_notes_for_context(
        agent_id=state.agent_id,
        tool_names=tool_names,
        error_slugs=error_slugs,
    )
    if not notes:
        return {}
    note_cards: list[dict[str, Any]] = []
    tool_tags = {
        f"tool:{_slugify_structural_value(name, default='tool')}"
        for name in tool_names
        if str(name or "").strip()
    }
    error_tags = {
        f"error:{_slugify_structural_value(slug, default='error')}"
        for slug in error_slugs
        if str(slug or "").strip()
    }
    for note in notes:
        note_tool_tags = [
            str(tag).strip()
            for tag in list(note.tags or [])
            if str(tag).strip().startswith("tool:")
        ]
        note_error_tags = [
            str(tag).strip()
            for tag in list(note.tags or [])
            if str(tag).strip().startswith("error:")
        ]
        note_cards.append(
            {
                "record_id": note.signature,
                "record_type": "improvement_note",
                "text": "improvement_note_ref",
                "meta": {
                    "signature": note.signature,
                    "status": note.status,
                    "source": note.source,
                    "guidance": note.guidance,
                    "occurrence_count": int(note.occurrence_count),
                    "updated_at": note.updated_at,
                    "tool_slugs": [tag.removeprefix("tool:") for tag in note_tool_tags],
                    "error_slugs": [
                        tag.removeprefix("error:") for tag in note_error_tags
                    ],
                },
            }
        )
    return {
        "improvement_note_cards": note_cards,
        "improvement_note_tool_tags": sorted(tool_tags),
        "improvement_note_error_tags": sorted(error_tags),
    }


def _strategy_outcome_overlay(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    hints: dict[str, Any],
) -> dict[str, Any]:
    strategy_id = str(
        getattr(state, "working_act_profile", None)
        or getattr(state, "active_mode_name", None)
        or ""
    ).strip()
    if not strategy_id:
        return {}
    capability_category = str(hints.get("capability_category", "") or "").strip()
    intent_category = str(getattr(state, "decision_reason_code", "") or "").strip()
    context_service = getattr(getattr(runner, "context_api", None), "service", None)
    memctl = getattr(context_service, "_memctl", None)
    list_cards = getattr(memctl, "list_cross_session_memory_cards_by_type", None)
    if not callable(list_cards):
        return {}
    cards = list_cards(
        agent_id=state.agent_id,
        record_types=["strategy_outcome"],
        limit=CONTEXT_STRATEGY_OUTCOME_LIMIT,
    )
    if not cards:
        return {}
    payloads: list[dict[str, Any]] = []
    for card in cards:
        payloads.append(
            {
                "record_id": str(getattr(card, "record_id", "") or "").strip(),
                "record_type": "strategy_outcome",
                "text": str(
                    getattr(card, "text", "") or "strategy_outcome_ref"
                ).strip(),
                "meta": dict(getattr(card, "meta", {}) or {}),
            }
        )
    return {
        "strategy_outcome_cards": payloads,
        "strategy_outcome_strategy_id": strategy_id,
        "strategy_outcome_capability_category": capability_category,
        "strategy_outcome_intent_category": intent_category,
    }


def _post_completion_critique_overlay(
    runner: "BrainRunner",
    *,
    state: WorkingState,
) -> dict[str, Any]:
    context_service = getattr(getattr(runner, "context_api", None), "service", None)
    memctl = getattr(context_service, "_memctl", None)
    list_cards = getattr(memctl, "list_cross_session_memory_cards_by_type", None)
    if not callable(list_cards):
        return {}
    cards = list_cards(
        agent_id=state.agent_id,
        record_types=["post_completion_critique"],
        limit=CONTEXT_POST_COMPLETION_CRITIQUE_LIMIT,
    )
    if not cards:
        return {}
    payloads: list[dict[str, Any]] = []
    for card in cards:
        payloads.append(
            {
                "record_id": str(getattr(card, "record_id", "") or "").strip(),
                "record_type": "post_completion_critique",
                "text": str(
                    getattr(card, "text", "") or "post_completion_critique_ref"
                ).strip(),
                "meta": dict(getattr(card, "meta", {}) or {}),
            }
        )
    intent_ids = [
        str(getattr(item, "intent_id", "") or "").strip()
        for item in list(getattr(state, "intent_execution_states", []) or [])
        if str(getattr(item, "intent_id", "") or "").strip()
    ]
    sub_intents = [
        str(item).strip()
        for item in list(getattr(state, "decision_sub_intents", []) or [])
        if str(item).strip()
    ]
    route_chosen = str(getattr(state, "active_mode_name", "") or "").strip()
    return {
        "post_completion_critique_cards": payloads,
        "post_completion_critique_intent_ids": intent_ids,
        "post_completion_critique_sub_intents": sub_intents,
        "post_completion_critique_route": route_chosen,
    }


def _overlay_result_has_facts(result: Any) -> bool:
    artifact_refs = list(getattr(result, "artifact_refs", []) or [])
    memory_refs = list(getattr(result, "memory_refs", []) or [])
    outputs = getattr(result, "outputs", None)
    facts = getattr(result, "facts", None)
    return bool(artifact_refs or memory_refs or outputs or facts)


def _adaptive_low_progress_counts(state: WorkingState) -> tuple[int, int]:
    module_state = getattr(state, STATE_KEY_MODULE_STATE, {}) or {}
    if not isinstance(module_state, dict):
        return (0, 0)
    adaptive_loop = module_state.get("adaptive_loop")
    if not isinstance(adaptive_loop, dict):
        return (0, 0)
    raw_history = adaptive_loop.get("tool_call_history")
    history = raw_history if isinstance(raw_history, list) else []
    hashes: list[str] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        args_hash = str(item.get("args_hash") or "").strip()
        if args_hash:
            hashes.append(args_hash)
    if not hashes:
        return (0, 0)
    counts: dict[str, int] = {}
    for args_hash in hashes:
        counts[args_hash] = counts.get(args_hash, 0) + 1
    repeated_arg_signature_count = sum(
        max(0, count - 1) for count in counts.values() if count > 1
    )
    raw_budgets = adaptive_loop.get("budgets_consumed")
    budgets = raw_budgets if isinstance(raw_budgets, dict) else {}
    total_tool_calls = int(budgets.get("tool_calls", len(hashes)) or 0)
    unique_tool_call_count_delta = max(0, total_tool_calls - len(counts))
    return repeated_arg_signature_count, unique_tool_call_count_delta


def _low_progress_overlay(
    runner: "BrainRunner",
    *,
    state: WorkingState,
) -> dict[str, Any]:
    cfg = getattr(getattr(runner, "meta_engine", None), "cfg", None) or MetaConfig()
    retries = getattr(state, "retries_for_step", {}) or {}
    iterations_without_new_typed_record = sum(
        int(value or 0) for value in retries.values()
    )
    target_result = getattr(state, "last_result", None)
    if target_result is None or _overlay_result_has_facts(target_result):
        return {}
    repeated_arg_signature_count, unique_tool_call_count_delta = (
        _adaptive_low_progress_counts(state)
    )
    if (
        iterations_without_new_typed_record
        < cfg.low_progress_iterations_without_new_typed_record_threshold
        or repeated_arg_signature_count
        < cfg.low_progress_repeated_arg_signature_threshold
        or unique_tool_call_count_delta
        < cfg.low_progress_unique_tool_call_count_delta_threshold
    ):
        return {}
    signal = LowProgressSignal(
        iterations_without_new_typed_record=iterations_without_new_typed_record,
        repeated_arg_signature_count=repeated_arg_signature_count,
        unique_tool_call_count_delta=unique_tool_call_count_delta,
    )
    return {"low_progress_signal": signal.model_dump(mode="json")}


def _learning_loop_metric_overlay(
    state: WorkingState,
    overlay: dict[str, Any],
) -> dict[str, Any]:
    note_count = len(list(overlay.get("improvement_note_cards") or []))
    strategy_count = len(list(overlay.get("strategy_outcome_cards") or []))
    critique_count = len(list(overlay.get("post_completion_critique_cards") or []))
    metric = LearningLoopMetric(
        readiness=(
            "ready" if note_count or strategy_count or critique_count else "partial"
        ),
        improvement_note_count=note_count,
        strategy_outcome_count=strategy_count,
        decision_memory_ref_count=len(
            list(getattr(state, "decision_memory_refs", []) or [])
        ),
        cross_session_strategy_outcomes_present=bool(strategy_count),
    )
    return {"learning_loop_metric": metric.model_dump(mode="json")}


def _record_outcome_attribution_snapshot(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    purpose: str,
    context: dict[str, Any],
) -> None:
    if purpose not in {"decide", "plan"}:
        return
    config = getattr(
        getattr(runner, "options", None), "outcome_attribution_config", None
    )
    if not getattr(config, "enabled", True):
        state.decision_memory_refs = []
        state.decision_context_pack_version = None
        state.decision_context_recorded_at = None
        return
    raw_manifest = context.get("context_manifest")
    if not isinstance(raw_manifest, dict):
        state.decision_memory_refs = []
        state.decision_context_pack_version = (
            str(context.get("pack_version") or "").strip() or None
        )
        state.decision_context_recorded_at = iso_now()
        return
    aggregated: list[Any] = list(raw_manifest.get("memory") or [])
    aggregated.extend(list(raw_manifest.get("recalled_memory") or []))
    if getattr(config, "include_fact_refs", True):
        aggregated.extend(list(raw_manifest.get("facts") or []))
    if getattr(config, "include_procedure_refs", True):
        aggregated.extend(list(raw_manifest.get("procedures") or []))
    max_refs = max(1, int(getattr(config, "max_memory_refs_per_command", 12) or 12))
    state.decision_memory_refs = _dedupe_text_values(aggregated)[:max_refs]
    pack_version = str(
        context.get("pack_version") or raw_manifest.get("pack_version") or ""
    ).strip()
    state.decision_context_pack_version = pack_version or None
    state.decision_context_recorded_at = iso_now()


def build_context(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    purpose: str,
    budget: dict[str, Any],
    hints: dict[str, Any] | None,
    logger: CanonicalEventLogger,
    mode_name: str | None = None,
) -> dict[str, Any]:
    if runner.context_api is None:
        return {}
    if hints is None:
        hints = {}

    if state.unresolved_clarify_items:
        hints["pending_clarifications"] = [
            {"id": q.id, "question": q.question} for q in state.unresolved_clarify_items
        ]
    if state.clarify_responses:
        hints["clarification_responses"] = state.clarify_responses
    pending_llm_clarify_context = getattr(state, "pending_llm_clarify_context", None)
    latest_user_reply = str(hints.get("user_input", "") or "").strip()
    continuation_guidance = str(
        getattr(state, "post_action_user_message", "") or ""
    ).strip()
    if (
        purpose == "decide"
        and pending_llm_clarify_context is not None
        and not state.unresolved_clarify_items
        and latest_user_reply
    ):
        hints["pending_conversational_clarification"] = {
            "original_user_input": str(
                getattr(pending_llm_clarify_context, "original_user_input", "") or ""
            ).strip(),
            "inferred_goal": str(
                getattr(pending_llm_clarify_context, "inferred_goal", "") or ""
            ).strip(),
            "known_context": dict(
                getattr(pending_llm_clarify_context, "known_context", {}) or {}
            ),
            "unresolved_question": str(
                getattr(pending_llm_clarify_context, "unresolved_question", "") or ""
            ).strip(),
            "clarify_question": str(
                getattr(pending_llm_clarify_context, "clarify_question", "") or ""
            ).strip(),
            "user_reply": latest_user_reply,
        }
    if purpose == "decide" and continuation_guidance and not latest_user_reply:
        hints["continuation_guidance"] = continuation_guidance
    active_mode_name = str(
        mode_name or getattr(state, "active_mode_name", None) or ""
    ).strip()
    if active_mode_name:
        hints["mode_name"] = active_mode_name
    gateway_ctx = str(getattr(state, "gateway_system_context", "") or "").strip()
    if gateway_ctx:
        hints["gateway_system_context"] = gateway_ctx
    profile = getattr(runner, "profile", None)
    if getattr(profile, "model_capability_overrides", None):
        hints["model_capability_overrides"] = profile.model_capability_overrides
    thinking_hints = _build_thinking_hints(runner, purpose=purpose, hints=hints)
    for key, value in thinking_hints.items():
        hints.setdefault(key, value)
    live_state_overlay = dict(hints.get("live_state_overlay") or {})
    live_state_overlay.update(_improvement_note_overlay(runner, state=state))
    live_state_overlay.update(
        _strategy_outcome_overlay(runner, state=state, hints=hints)
    )
    live_state_overlay.update(_post_completion_critique_overlay(runner, state=state))
    live_state_overlay.update(_low_progress_overlay(runner, state=state))
    live_state_overlay.update(_learning_loop_metric_overlay(state, live_state_overlay))
    if live_state_overlay:
        hints["live_state_overlay"] = live_state_overlay
    hints.update(_budget_telemetry_overlay(runner, state=state))

    original_hint_keys = set(hints.keys())
    sanitized_hints: dict[str, Any] = {}
    for key, value in hints.items():
        if _is_phase_hint_allowed(purpose=purpose, key=key):
            sanitized_hints[key] = value
    dropped_keys = sorted(original_hint_keys.difference(sanitized_hints.keys()))
    hints = sanitized_hints
    if dropped_keys:
        logger.emit(
            "context.phase_hints.filtered",
            {
                "purpose": purpose,
                "dropped_keys": dropped_keys,
            },
            trace_id=state.trace_id,
            status="info",
        )

    context = runner.context_api.build(
        session_id=state.session_id,
        agent_id=state.agent_id,
        purpose=purpose,
        budget=budget,
        hints=hints,
    )
    runner._emit_brain_operation(
        session_id=state.session_id,
        turn_id=str(state.trace_id or "").strip(),
        operation="llm_pack",
        extra={"purpose": purpose},
    )
    if "context_manifest" in context:
        _record_outcome_attribution_snapshot(
            runner,
            state=state,
            purpose=purpose,
            context=context,
        )
        manifest_payload: dict[str, Any]
        raw_manifest = context["context_manifest"]
        if isinstance(raw_manifest, dict):
            manifest_payload = dict(raw_manifest)
        else:
            manifest_payload = {"manifest": raw_manifest}

        llm_call_id = context.get("llm_call_id") or hints.get("_llm_call_id")
        if llm_call_id:
            validation = runner._validate_call_order(
                llm_call_id, "context.manifest.created"
            )
            if not validation["valid"]:
                logger.emit(
                    "context.manifest.validation_failed",
                    {
                        "llm_call_id": llm_call_id,
                        "reason": validation["reason"],
                        "manifest": manifest_payload,
                    },
                    trace_id=state.trace_id,
                    status="warning",
                )
        logger.emit(
            "context.manifest.created",
            manifest_payload,
            trace_id=state.trace_id,
        )
    return context
