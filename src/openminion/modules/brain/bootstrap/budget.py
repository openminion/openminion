from typing import TYPE_CHECKING, Any

from openminion.modules.brain.execution.delegation import _runner_delegate
from openminion.modules.brain.diagnostics.events import CanonicalEventLogger
from openminion.modules.brain.constants import (
    CONTEXT_BUDGET_TIER_FULL,
    CONTEXT_BUDGET_TIER_MEDIUM,
)
from openminion.modules.brain.schemas import Decision, WorkingState
from openminion.modules.brain.tools.schema import tool_schema_stub
from .context import (
    _compact_decide_mode_descriptions,
    _drop_example_style_overrides,
    _rebuild_decide_context_with_hints,
)
from .recovery import _respond_decision

if TYPE_CHECKING:  # pragma: no cover - typing only
    from openminion.modules.brain.runner import BrainRunner


def infer_context_budget_tier(
    *,
    intent: str,
    session_snapshot: dict[str, Any],
    effective_skill_count: int,
) -> str:
    del intent
    if bool(session_snapshot.get("has_prior_transcript")) or bool(
        session_snapshot.get("has_session_summary")
    ):
        return CONTEXT_BUDGET_TIER_FULL
    if effective_skill_count >= 3:
        return CONTEXT_BUDGET_TIER_FULL
    return CONTEXT_BUDGET_TIER_MEDIUM


def trim_decide_context_to_budget(
    *,
    runner: "BrainRunner",
    state: WorkingState,
    logger: CanonicalEventLogger,
    model: str,
    budget_max_tokens: int,
    hints: dict[str, Any],
    context: dict[str, Any],
    estimate: int,
    user_input: str | None,
) -> tuple[dict[str, Any], int, Decision | None]:
    if estimate <= state.budgets_remaining.tokens:
        return context, estimate, None

    runtime_schemas = hints.get("runtime_tool_schemas")
    if isinstance(runtime_schemas, list) and runtime_schemas:
        compact_schemas = [tool_schema_stub(item) for item in runtime_schemas]
        hints["runtime_tool_schemas"] = compact_schemas
        context = _runner_delegate(
            "_build_context",
            runner,
            state=state,
            purpose="decide",
            budget={"max_tokens": budget_max_tokens},
            hints=hints,
            logger=logger,
        )
        compact_estimate = _runner_delegate(
            "_estimate_tokens", runner, model=model, context=context
        )
        logger.emit(
            "llm.context.trimmed",
            {
                "purpose": "decide",
                "strategy": "runtime_tool_schemas_stubbed",
                "from_tools": len(runtime_schemas),
                "to_tools": len(compact_schemas),
                "estimate_before": estimate,
                "estimate_after": compact_estimate,
            },
            trace_id=state.trace_id,
        )
        estimate = compact_estimate

    if estimate > state.budgets_remaining.tokens:
        runtime_schemas = hints.get("runtime_tool_schemas")
        if isinstance(runtime_schemas, list) and runtime_schemas:
            shortlisted_schemas = _runner_delegate(
                "_build_prompt_tool_schemas", runner, user_input=user_input
            )
            if shortlisted_schemas:
                hints["runtime_tool_schemas"] = shortlisted_schemas
                context = _runner_delegate(
                    "_build_context",
                    runner,
                    state=state,
                    purpose="decide",
                    budget={"max_tokens": budget_max_tokens},
                    hints=hints,
                    logger=logger,
                )
                shortlisted_estimate = _runner_delegate(
                    "_estimate_tokens",
                    runner,
                    model=model,
                    context=context,
                )
                logger.emit(
                    "llm.context.trimmed",
                    {
                        "purpose": "decide",
                        "strategy": "runtime_tool_schemas_shortlisted",
                        "from_tools": len(runtime_schemas),
                        "to_tools": len(shortlisted_schemas),
                        "estimate_before": estimate,
                        "estimate_after": shortlisted_estimate,
                    },
                    trace_id=state.trace_id,
                )
                estimate = shortlisted_estimate

    if estimate > state.budgets_remaining.tokens:
        runtime_schemas = hints.get("runtime_tool_schemas")
        if isinstance(runtime_schemas, list) and runtime_schemas:
            hints.pop("runtime_tool_schemas", None)
            context = _runner_delegate(
                "_build_context",
                runner,
                state=state,
                purpose="decide",
                budget={"max_tokens": budget_max_tokens},
                hints=hints,
                logger=logger,
            )
            no_tool_estimate = _runner_delegate(
                "_estimate_tokens", runner, model=model, context=context
            )
            logger.emit(
                "llm.context.trimmed",
                {
                    "purpose": "decide",
                    "strategy": "runtime_tool_schemas_removed",
                    "removed_tools": len(runtime_schemas),
                    "estimate_before": estimate,
                    "estimate_after": no_tool_estimate,
                },
                trace_id=state.trace_id,
            )
            estimate = no_tool_estimate

    if estimate > state.budgets_remaining.tokens:
        compact_modes = _compact_decide_mode_descriptions(
            hints.get("decision_route_descriptions")
        )
        if compact_modes is not None:
            hints["decision_route_descriptions"] = compact_modes
            context, compact_mode_estimate = _rebuild_decide_context_with_hints(
                runner,
                state=state,
                logger=logger,
                budget_max_tokens=budget_max_tokens,
                hints=hints,
            )
            logger.emit(
                "llm.context.trimmed",
                {
                    "purpose": "decide",
                    "strategy": "decision_route_descriptions_compacted",
                    "estimate_before": estimate,
                    "estimate_after": compact_mode_estimate,
                    "mode_count": len(compact_modes),
                },
                trace_id=state.trace_id,
            )
            estimate = compact_mode_estimate

    if estimate > state.budgets_remaining.tokens:
        compact_style = _drop_example_style_overrides(hints.get("style_overrides"))
        if compact_style is not None:
            hints["style_overrides"] = compact_style
            context, style_estimate = _rebuild_decide_context_with_hints(
                runner,
                state=state,
                logger=logger,
                budget_max_tokens=budget_max_tokens,
                hints=hints,
            )
            logger.emit(
                "llm.context.trimmed",
                {
                    "purpose": "decide",
                    "strategy": "style_overrides_examples_removed",
                    "estimate_before": estimate,
                    "estimate_after": style_estimate,
                    "style_override_count": len(compact_style),
                },
                trace_id=state.trace_id,
            )
            estimate = style_estimate

    if estimate > state.budgets_remaining.tokens:
        style_overrides = hints.get("style_overrides")
        if isinstance(style_overrides, dict) and style_overrides:
            hints.pop("style_overrides", None)
            context, no_style_estimate = _rebuild_decide_context_with_hints(
                runner,
                state=state,
                logger=logger,
                budget_max_tokens=budget_max_tokens,
                hints=hints,
            )
            logger.emit(
                "llm.context.trimmed",
                {
                    "purpose": "decide",
                    "strategy": "style_overrides_removed",
                    "estimate_before": estimate,
                    "estimate_after": no_style_estimate,
                },
                trace_id=state.trace_id,
            )
            estimate = no_style_estimate

    if estimate > state.budgets_remaining.tokens:
        return (
            context,
            estimate,
            _respond_decision(
                confidence=1.0,
                reason_code="token_budget_exceeded",
                answer=(
                    "I couldn't continue safely because the decision context "
                    "exceeded the available token budget for this turn."
                ),
            ),
        )

    return context, estimate, None
