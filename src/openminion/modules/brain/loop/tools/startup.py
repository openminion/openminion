from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from openminion.modules.llm.schemas import Message

from .cache import LoopCache
from .memory_templates import LoopTemplate, build_template_hint, match_templates
from .prefetch import PrefetchPredictor
from .profiler import LoopProfiler
from .snapshot import LoopSnapshot
from openminion.modules.brain.constants import STATE_KEY_MODULE_STATE

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .contracts import (
        AdaptiveToolLoopContext,
        AdaptiveToolLoopProfile,
        AdaptiveToolLoopState,
    )


@dataclass(slots=True)
class AdaptiveLoopRuntimeState:
    resumed: bool
    resume_iteration: int | None
    loop_cache: LoopCache
    budget_hint_injected: bool
    iteration_tool_sequences: list[tuple[str, ...]]
    loop_profiler: LoopProfiler
    prefetch_predictor: PrefetchPredictor | None
    prefetch_pending: str | None


def _loop_template_match_tags(loop_ctx: "AdaptiveToolLoopContext") -> tuple[str, ...]:
    state = getattr(loop_ctx, "state", None)
    if state is None:
        return ()
    tags: list[str] = []
    for raw in list(getattr(state, "decision_sub_intents", []) or []):
        tag = str(raw or "").strip()
        if tag and tag not in tags:
            tags.append(tag)
    for item in list(getattr(state, "intent_execution_states", []) or []):
        tag = str(getattr(item, "intent_id", "") or "").strip()
        if tag and tag not in tags:
            tags.append(tag)
    return tuple(tags)


def _restore_loop_snapshot(
    *,
    loop_ctx: "AdaptiveToolLoopContext",
    loop_state: "AdaptiveToolLoopState",
    profile: "AdaptiveToolLoopProfile",
    model: str,
    turn_scope_id: str | None,
) -> tuple[bool, int | None]:
    resumed = False
    resume_iteration: int | None = None
    module_state = getattr(
        getattr(loop_ctx, "state", None), STATE_KEY_MODULE_STATE, None
    )
    if not isinstance(module_state, dict):
        return resumed, resume_iteration
    existing = module_state.get("adaptive_loop")
    if not isinstance(existing, dict):
        return resumed, resume_iteration
    try:
        snapshot = LoopSnapshot.from_dict(existing)
        if (
            turn_scope_id
            and snapshot.turn_scope_id == turn_scope_id
            and snapshot.profile_name == profile.profile_name
            and snapshot.model == model
            and snapshot.allowed_tools == (profile.allowed_tools or frozenset())
        ):
            loop_state.iteration = snapshot.iteration_index + 1
            loop_state.llm_calls = snapshot.budgets_consumed.get("llm_calls", 0)
            loop_state.total_tool_calls = snapshot.budgets_consumed.get("tool_calls", 0)
            resumed = True
            resume_iteration = snapshot.iteration_index
    except Exception:  # noqa: BLE001
        pass
    finally:
        module_state.pop("adaptive_loop", None)
    return resumed, resume_iteration


def initialize_loop_runtime_state(
    *,
    loop_ctx: "AdaptiveToolLoopContext",
    loop_state: "AdaptiveToolLoopState",
    profile: "AdaptiveToolLoopProfile",
    model: str,
    turn_scope_id: str | None,
) -> AdaptiveLoopRuntimeState:
    resumed, resume_iteration = _restore_loop_snapshot(
        loop_ctx=loop_ctx,
        loop_state=loop_state,
        profile=profile,
        model=model,
        turn_scope_id=turn_scope_id,
    )

    loop_state.scratchpad["resumed_from_snapshot"] = resumed
    loop_state.scratchpad["resume_iteration_index"] = resume_iteration
    loop_state.scratchpad["max_macro_corrections"] = int(
        profile.max_macro_corrections or 0
    )

    loop_cache = LoopCache()
    budget_hint_injected = False
    iteration_tool_sequences: list[tuple[str, ...]] = []
    loop_profiler = LoopProfiler()
    prefetch_predictor: PrefetchPredictor | None = (
        PrefetchPredictor() if profile.speculative_prefetch else None
    )
    prefetch_pending: str | None = None

    if profile.use_memory_templates:
        existing_templates_raw = loop_state.scratchpad.get("loop_templates", [])
        if isinstance(existing_templates_raw, list) and existing_templates_raw:
            existing_templates: list[LoopTemplate] = [
                LoopTemplate.from_dict(item)
                for item in existing_templates_raw
                if isinstance(item, dict)
            ]
            matched = match_templates(
                existing_templates,
                _loop_template_match_tags(loop_ctx),
            )
            hint = build_template_hint(matched)
            if hint:
                loop_state.messages.append(Message(role="system", content=hint))

    return AdaptiveLoopRuntimeState(
        resumed=resumed,
        resume_iteration=resume_iteration,
        loop_cache=loop_cache,
        budget_hint_injected=budget_hint_injected,
        iteration_tool_sequences=iteration_tool_sequences,
        loop_profiler=loop_profiler,
        prefetch_predictor=prefetch_predictor,
        prefetch_pending=prefetch_pending,
    )
