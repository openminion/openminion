from dataclasses import dataclass
from types import SimpleNamespace

from openminion.modules.brain.constants import (
    BRAIN_ACT_PROFILE_CODING,
    BRAIN_ACT_PROFILE_GENERAL,
    BRAIN_ACT_PROFILE_ORCHESTRATE,
    BRAIN_ACT_PROFILE_RESEARCH,
    BRAIN_INTERNAL_MODE_ACT_ADAPTIVE,
    BRAIN_INTERNAL_MODE_ACT_ORCHESTRATE,
    BRAIN_INTERNAL_MODE_ACT_RESEARCH,
    BRAIN_INTERNAL_MODE_EXECUTION_TARGET_DELEGATED,
    BRAIN_EXECUTION_TARGET_DELEGATED,
    BRAIN_EXECUTION_TARGET_LOCAL,
)
from openminion.modules.brain.execution.orchestrate.handler import OrchestrateMode
from openminion.modules.brain.execution.loop_contracts import ExecutionContext
from openminion.modules.brain.execution.targets import (
    build_delegated_decision,
    is_delegated_target,
)
from openminion.modules.brain.loop.adaptive import ActLoopMode
from openminion.modules.brain.loop.strategies.coding import build_coding_decision
from openminion.modules.brain.loop.strategies.research import (
    ResearchMode,
    build_research_decision,
)
from openminion.modules.brain.schemas import ExecutionTargetPayload

from .strategy import resolve_loop_strategy


@dataclass(frozen=True, slots=True)
class ActInternalDispatch:
    handler: object
    decision: object


@dataclass(frozen=True, slots=True)
class ResolvedActRoute:
    act_profile: str
    execution_target: ExecutionTargetPayload
    source: str


_CONTINUATION_REASON_CODES = {
    "resume_existing_plan",
    "confirmation_replay",
    "confirmation_replay_validation",
    "plan_continuation_after_deny",
    "delegate_async_resume",
}


def _should_route_seeded_confirmation_replay_to_general(*, decision: object) -> bool:
    reason_code = str(getattr(decision, "reason_code", "") or "").strip().lower()
    if reason_code not in {"confirmation_replay", "confirmation_replay_validation"}:
        return False
    return bool(list(getattr(decision, "_seeded_commands", []) or []))


_VALID_ACT_PROFILES = {
    BRAIN_ACT_PROFILE_GENERAL,
    BRAIN_ACT_PROFILE_CODING,
    BRAIN_ACT_PROFILE_RESEARCH,
    BRAIN_ACT_PROFILE_ORCHESTRATE,
}


def _copy_decision_sidecars(*, decision: object, internal: object) -> object:
    seeded_commands = list(getattr(decision, "_seeded_commands", []) or [])
    if seeded_commands:
        internal._seeded_commands = seeded_commands
    entry_response = getattr(decision, "_entry_response", None)
    if entry_response is not None:
        internal._entry_response = entry_response
    return internal


def build_orchestrate_decision(*, decision: object) -> object:
    return _copy_decision_sidecars(
        decision=decision,
        internal=SimpleNamespace(
            confidence=float(getattr(decision, "confidence", 1.0) or 1.0),
            reason_code=str(getattr(decision, "reason_code", "") or "").strip()
            or "act_orchestrate",
            sub_intents=list(getattr(decision, "sub_intents", []) or []),
            subtasks=list(getattr(decision, "subtasks", []) or []),
            question=None,
            answer=None,
            rationale=str(getattr(decision, "rationale", "") or "").strip(),
        ),
    )


def build_general_act_decision(*, decision: object) -> object:
    return _copy_decision_sidecars(
        decision=decision,
        internal=SimpleNamespace(
            act_profile=BRAIN_ACT_PROFILE_GENERAL,
            confidence=float(getattr(decision, "confidence", 1.0) or 1.0),
            reason_code=str(getattr(decision, "reason_code", "") or "").strip()
            or "act_loop",
            sub_intents=list(getattr(decision, "sub_intents", []) or []),
            rationale=str(getattr(decision, "rationale", "") or "").strip(),
            question=None,
            answer=None,
        ),
    )


def _normalized_profile(value: object) -> str:
    text = str(value or "").strip().lower()
    if text in _VALID_ACT_PROFILES:
        return text
    return ""


def _normalized_execution_target(value: object) -> ExecutionTargetPayload | None:
    if value in (None, ""):
        return None
    if isinstance(value, ExecutionTargetPayload):
        return value
    kind = str(getattr(value, "kind", "") or "").strip().lower()
    if not kind:
        return None
    payload = {
        "kind": kind,
        "target_agent_id": str(getattr(value, "target_agent_id", "") or "").strip(),
        "target_capability": str(getattr(value, "target_capability", "") or "").strip(),
        "expect_async": bool(getattr(value, "expect_async", False)),
    }
    return ExecutionTargetPayload.model_validate(payload)


def _should_reuse_persisted_route(
    *,
    state: object,
    decision: object,
    has_new_user_input: bool,
) -> bool:
    if not getattr(state, "working_act_profile", None) and not getattr(
        state, "working_execution_target_kind", None
    ):
        return False
    if not has_new_user_input:
        return True
    reason_code = str(getattr(decision, "reason_code", "") or "").strip().lower()
    if reason_code in _CONTINUATION_REASON_CODES:
        return True
    return bool(getattr(state, "plan", None))


def resolve_working_act_route(
    *,
    decision: object,
    state: object,
    default_act_profile: str | None = None,
    has_new_user_input: bool = False,
) -> ResolvedActRoute:
    fixed_profile = _normalized_profile(default_act_profile)
    persisted_profile = _normalized_profile(getattr(state, "working_act_profile", None))
    persisted_target_kind = (
        str(getattr(state, "working_execution_target_kind", "") or "").strip().lower()
    )
    persisted_target = (
        ExecutionTargetPayload(
            kind=persisted_target_kind,
            target_agent_id=str(
                getattr(state, "delegation_target_agent_id", "") or ""
            ).strip(),
        )
        if persisted_target_kind
        in {BRAIN_EXECUTION_TARGET_LOCAL, BRAIN_EXECUTION_TARGET_DELEGATED}
        else None
    )
    explicit_target = _normalized_execution_target(
        getattr(decision, "execution_target", None)
    )
    explicit_subtasks = list(getattr(decision, "subtasks", []) or [])
    has_orchestrate_subtasks = len(explicit_subtasks) >= 2
    explicit_profile = _normalized_profile(getattr(decision, "act_profile", None))
    reuse_persisted_route = _should_reuse_persisted_route(
        state=state,
        decision=decision,
        has_new_user_input=has_new_user_input,
    )

    execution_target = explicit_target
    execution_source = "decision_execution_target" if explicit_target else ""
    if reuse_persisted_route:
        if persisted_target is not None:
            execution_target = persisted_target
            execution_source = "resume_checkpoint"

    if execution_target is None:
        execution_target = ExecutionTargetPayload(kind=BRAIN_EXECUTION_TARGET_LOCAL)
        execution_source = "runtime_default_local"

    if fixed_profile:
        profile = fixed_profile
        profile_source = "config_default_act_profile"
    elif reuse_persisted_route and persisted_profile:
        profile = persisted_profile
        profile_source = "resume_checkpoint"
    elif has_orchestrate_subtasks:
        profile = BRAIN_ACT_PROFILE_ORCHESTRATE
        profile_source = "decision_subtasks"
    elif explicit_profile and (
        explicit_profile != BRAIN_ACT_PROFILE_ORCHESTRATE or has_orchestrate_subtasks
    ):
        profile = explicit_profile
        profile_source = "decision_act_profile"
    else:
        profile = BRAIN_ACT_PROFILE_GENERAL
        profile_source = "runtime_default_general"

    if (
        profile == BRAIN_ACT_PROFILE_RESEARCH
        and _should_route_seeded_confirmation_replay_to_general(decision=decision)
    ):
        profile = BRAIN_ACT_PROFILE_GENERAL
        profile_source = "confirmation_replay_seeded_general"

    source = (
        execution_source
        if execution_target.kind == BRAIN_EXECUTION_TARGET_DELEGATED
        else profile_source
    )
    return ResolvedActRoute(
        act_profile=profile,
        execution_target=execution_target,
        source=source,
    )


def apply_resolved_act_route(
    *,
    decision: object,
    route: ResolvedActRoute,
) -> object:
    setattr(decision, "act_profile", route.act_profile)
    setattr(decision, "execution_target", route.execution_target)
    return decision


def goal_from_context(ctx: ExecutionContext) -> str:
    return (
        str(ctx.user_input or "").strip()
        or str(getattr(ctx.state, "goal", "") or "").strip()
        or "Complete the requested act task."
    )


def build_internal_decision(ctx: ExecutionContext) -> object:
    decision = ctx.decision
    if is_delegated_target(getattr(decision, "execution_target", None)):
        return build_delegated_decision(
            decision=decision,
            goal=goal_from_context(ctx),
        )
    act_profile = str(getattr(decision, "act_profile", "") or "").strip().lower()
    if act_profile == BRAIN_ACT_PROFILE_ORCHESTRATE:
        return build_orchestrate_decision(decision=decision)
    if act_profile == BRAIN_ACT_PROFILE_CODING:
        return build_coding_decision(
            decision=decision,
            goal=goal_from_context(ctx),
        )
    if act_profile == BRAIN_ACT_PROFILE_RESEARCH:
        return build_research_decision(
            decision=decision,
            query=goal_from_context(ctx),
        )
    if act_profile != BRAIN_ACT_PROFILE_GENERAL:
        raise ValueError(
            f"Runtime must resolve a supported act_profile for mode='act': {act_profile!r}"
        )
    return build_general_act_decision(decision=decision)


def build_internal_handler(ctx: ExecutionContext):
    decision = ctx.decision
    if is_delegated_target(getattr(decision, "execution_target", None)):
        from openminion.modules.brain.execution.targets.delegated.handler import (  # noqa: PLC0415
            DelegateMode,
        )

        return DelegateMode()
    act_profile = str(getattr(decision, "act_profile", "") or "").strip().lower()
    strategy = resolve_loop_strategy(act_profile)
    if strategy.mode_name == BRAIN_INTERNAL_MODE_ACT_ORCHESTRATE:
        return OrchestrateMode()
    if strategy.mode_name == BRAIN_INTERNAL_MODE_ACT_RESEARCH:
        return ResearchMode()
    if act_profile not in {
        BRAIN_ACT_PROFILE_GENERAL,
        BRAIN_ACT_PROFILE_CODING,
    }:
        raise ValueError(
            f"Runtime must resolve a supported act_profile for mode='act': {act_profile!r}"
        )
    return ActLoopMode()


def resolve_internal_mode_name(ctx: ExecutionContext) -> str:
    decision = ctx.decision
    if is_delegated_target(getattr(decision, "execution_target", None)):
        return BRAIN_INTERNAL_MODE_EXECUTION_TARGET_DELEGATED
    strategy = resolve_loop_strategy(
        str(getattr(decision, "act_profile", "") or "").strip().lower()
    )
    if strategy.mode_name in {
        BRAIN_INTERNAL_MODE_ACT_ADAPTIVE,
        BRAIN_INTERNAL_MODE_ACT_RESEARCH,
        BRAIN_INTERNAL_MODE_ACT_ORCHESTRATE,
    }:
        return strategy.mode_name
    if strategy.act_profile == BRAIN_ACT_PROFILE_CODING:
        return BRAIN_INTERNAL_MODE_ACT_ADAPTIVE
    raise ValueError(
        f"Runtime must resolve a supported act strategy for mode='act': {strategy!r}"
    )


def build_internal_dispatch(ctx: ExecutionContext) -> ActInternalDispatch:
    return ActInternalDispatch(
        handler=build_internal_handler(ctx),
        decision=build_internal_decision(ctx),
    )


__all__ = [
    "ActInternalDispatch",
    "ResolvedActRoute",
    "apply_resolved_act_route",
    "build_internal_decision",
    "build_internal_dispatch",
    "build_internal_handler",
    "goal_from_context",
    "resolve_internal_mode_name",
    "resolve_loop_strategy",
    "resolve_working_act_route",
]
