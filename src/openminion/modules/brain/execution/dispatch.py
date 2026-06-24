from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable

from ..config import fixed_act_profile_from_profile
from ..constants import (
    BRAIN_DECISION_ROUTE_ACT,
    BRAIN_DECISION_ROUTE_RESPOND,
    BRAIN_INTERNAL_MODE_ACT_ADAPTIVE,
    BRAIN_INTERNAL_MODE_ACT_CODING,
    BRAIN_INTERNAL_MODE_ACT_ORCHESTRATE,
    BRAIN_INTERNAL_MODE_ACT_RESEARCH,
    BRAIN_INTERNAL_MODE_EXECUTION_TARGET_DELEGATED,
    BRAIN_RESPOND_KIND_CLARIFY,
    BRAIN_STATE_DONE,
    BRAIN_STATE_WAITING_USER,
)
from .context import build_execution_context
from .continuation import is_resume_like_input
from .loop_contracts import ExecutionContext, ExecutionResult
from .lifecycle import dispatch_execution_call
from .preflight import ModePreparation, ValidationResult


@dataclass(frozen=True, slots=True)
class DirectExecutionOwner:
    mode_name: str
    ctx: ExecutionContext
    prepare_fn: Callable[..., ModePreparation | None] | None
    validate_fn: Callable[..., ValidationResult | None] | None
    execute_fn: Callable[[ExecutionContext], ExecutionResult]


def _decision_route_name(decision: Any) -> str:
    return str(getattr(decision, "route", getattr(decision, "mode", "")) or "").strip()


def _mode_config(runner: Any, mode_name: str) -> Any | None:
    profile = getattr(runner, "profile", None)
    mode_config = getattr(profile, "mode_config", None)
    if not isinstance(mode_config, dict):
        return None
    return mode_config.get(mode_name)


def _is_enabled(runner: Any, mode_name: str) -> bool:
    config = _mode_config(runner, mode_name)
    return bool(getattr(config, "enabled", True))


def _enforce_depth_limit(runner: Any, *, mode_name: str, depth: int) -> None:
    config = _mode_config(runner, mode_name)
    max_depth = getattr(config, "max_depth", None)
    if max_depth is not None and depth > int(max_depth):
        raise ValueError(
            f"Mode depth exceeded for {mode_name!r}: depth={depth}, max_depth={max_depth}"
        )


def _configure_handler(runner: Any, handler: Any, *, mode_name: str) -> Any:
    apply_mode_config = getattr(handler, "apply_mode_config", None)
    if callable(apply_mode_config):
        apply_mode_config(
            config=_mode_config(runner, mode_name),
            runner=runner,
            profile=getattr(runner, "profile", None),
        )
    return handler


def _build_owner(
    *,
    runner: Any,
    handler: Any,
    state: Any,
    decision: Any,
    user_input: str | None,
    logger: Any,
    mode_name: str | None = None,
    suppress_lifecycle_exit_statuses: bool = False,
) -> DirectExecutionOwner:
    resolved_mode_name = str(
        mode_name or getattr(handler, "mode_name", "") or ""
    ).strip()
    if not resolved_mode_name:
        raise ValueError("Direct execution owner requires mode_name")
    configured = _configure_handler(runner, handler, mode_name=resolved_mode_name)
    ctx = build_execution_context(
        runner,
        state=state,
        decision=decision,
        user_input=user_input,
        logger=logger,
        suppress_lifecycle_exit_statuses=suppress_lifecycle_exit_statuses,
    )
    prepare_fn = (
        getattr(configured, "prepare", None)
        if bool(getattr(configured, "has_prepare", False))
        else None
    )
    validate_fn = (
        getattr(configured, "validate", None)
        if bool(getattr(configured, "has_validate", False))
        else None
    )
    execute_fn = getattr(configured, "execute")
    return DirectExecutionOwner(
        mode_name=resolved_mode_name,
        ctx=ctx,
        prepare_fn=prepare_fn,
        validate_fn=validate_fn,
        execute_fn=execute_fn,
    )


def _respond_execute(ctx: ExecutionContext) -> ExecutionResult:
    respond_kind = str(getattr(ctx.decision, "respond_kind", "") or "").strip()
    reason_code = str(getattr(ctx.decision, "reason_code", "") or "").strip()
    # structural no-op path. When idle-tick enforcement coerces
    if reason_code == "pae_idle_tick_noop":
        from ..state import respond_structural_noop

        # `ExecutionContext._services` is a `RunnerExecutionServices`
        # whose `.runner` is the `BrainRunner` instance.
        services = getattr(ctx, "_services", None)
        runner = getattr(services, "runner", None)
        return ExecutionResult.from_step_output(
            respond_structural_noop(
                runner,
                state=ctx.state,
                logger=ctx.logger,
                status=BRAIN_STATE_DONE,
            )
        )

    text = ctx.direct_response()
    status = BRAIN_STATE_DONE
    if respond_kind == BRAIN_RESPOND_KIND_CLARIFY:
        status = BRAIN_STATE_WAITING_USER
    return ExecutionResult.from_step_output(
        ctx.respond(
            message=text,
            status=status,
        )
    )


def _respond_owner(
    *,
    runner: Any,
    state: Any,
    decision: Any,
    user_input: str | None,
    logger: Any,
    suppress_lifecycle_exit_statuses: bool = False,
) -> DirectExecutionOwner:
    ctx = build_execution_context(
        runner,
        state=state,
        decision=decision,
        user_input=user_input,
        logger=logger,
        suppress_lifecycle_exit_statuses=suppress_lifecycle_exit_statuses,
    )
    return DirectExecutionOwner(
        mode_name=BRAIN_DECISION_ROUTE_RESPOND,
        ctx=ctx,
        prepare_fn=None,
        validate_fn=None,
        execute_fn=_respond_execute,
    )


def _internal_handler_for_mode(mode_name: str) -> Any:
    normalized = str(mode_name or "").strip()
    if normalized == BRAIN_INTERNAL_MODE_EXECUTION_TARGET_DELEGATED:
        from ..execution.targets.delegated.handler import DelegateMode  # noqa: PLC0415

        return DelegateMode()
    if normalized == BRAIN_INTERNAL_MODE_ACT_ORCHESTRATE:
        from ..execution.orchestrate.handler import OrchestrateMode  # noqa: PLC0415

        return OrchestrateMode()
    if normalized == BRAIN_INTERNAL_MODE_ACT_RESEARCH:
        from ..loop.strategies.research.handler import ResearchMode  # noqa: PLC0415

        return ResearchMode()
    if normalized == BRAIN_INTERNAL_MODE_ACT_CODING:
        from ..loop.strategies.coding.handler import CodingMode  # noqa: PLC0415

        return CodingMode()
    if normalized == BRAIN_INTERNAL_MODE_ACT_ADAPTIVE:
        from ..loop.adaptive import ActLoopMode  # noqa: PLC0415

        return ActLoopMode()
    raise ValueError(f"Unsupported direct execution mode: {mode_name!r}")


def _act_owner(
    *,
    runner: Any,
    state: Any,
    decision: Any,
    user_input: str | None,
    logger: Any,
    suppress_lifecycle_exit_statuses: bool = False,
) -> DirectExecutionOwner:
    from ..bootstrap.resolve import (  # noqa: PLC0415
        apply_resolved_act_route,
        build_internal_dispatch,
        resolve_working_act_route,
    )

    route = getattr(decision, "_pre_resolved_act_route", None)
    if route is None:
        route = resolve_working_act_route(
            decision=decision,
            state=state,
            default_act_profile=fixed_act_profile_from_profile(
                getattr(runner, "profile", None)
            ),
            has_new_user_input=bool(str(user_input or "").strip()),
        )
    resolved_decision = apply_resolved_act_route(decision=decision, route=route)
    outer_ctx = build_execution_context(
        runner,
        state=state,
        decision=resolved_decision,
        user_input=user_input,
        logger=logger,
        suppress_lifecycle_exit_statuses=suppress_lifecycle_exit_statuses,
    )
    dispatch = build_internal_dispatch(outer_ctx)
    return _build_owner(
        runner=runner,
        handler=dispatch.handler,
        state=state,
        decision=dispatch.decision,
        user_input=user_input,
        logger=logger,
        mode_name=str(getattr(dispatch.handler, "mode_name", "") or "").strip() or None,
        suppress_lifecycle_exit_statuses=suppress_lifecycle_exit_statuses,
    )


def _direct_owner(
    *,
    runner: Any,
    state: Any,
    decision: Any,
    user_input: str | None,
    logger: Any,
    suppress_lifecycle_exit_statuses: bool = False,
) -> DirectExecutionOwner:
    mode_name = _decision_route_name(decision)
    if mode_name == BRAIN_DECISION_ROUTE_RESPOND:
        return _respond_owner(
            runner=runner,
            state=state,
            decision=decision,
            user_input=user_input,
            logger=logger,
            suppress_lifecycle_exit_statuses=suppress_lifecycle_exit_statuses,
        )
    if mode_name == BRAIN_DECISION_ROUTE_ACT:
        return _act_owner(
            runner=runner,
            state=state,
            decision=decision,
            user_input=user_input,
            logger=logger,
            suppress_lifecycle_exit_statuses=suppress_lifecycle_exit_statuses,
        )
    return _build_owner(
        runner=runner,
        handler=_internal_handler_for_mode(mode_name),
        state=state,
        decision=decision,
        user_input=user_input,
        logger=logger,
        mode_name=mode_name,
    )


def prepare_decision_direct(
    runner: Any,
    *,
    state: Any,
    decision: Any,
    user_input: str | None,
    logger: Any,
    emit_status_updates: bool = False,
) -> ModePreparation | None:
    owner = _direct_owner(
        runner=runner,
        state=state,
        decision=decision,
        user_input=user_input,
        logger=logger,
        suppress_lifecycle_exit_statuses=False,
    )
    if not _is_enabled(runner, owner.mode_name) or owner.prepare_fn is None:
        return None
    return owner.prepare_fn(owner.ctx, emit_status_updates=emit_status_updates)


def validate_decision_direct(
    runner: Any,
    *,
    state: Any,
    decision: Any,
    user_input: str | None,
    logger: Any,
    preparation: ModePreparation | None = None,
) -> ValidationResult | None:
    owner = _direct_owner(
        runner=runner,
        state=state,
        decision=decision,
        user_input=user_input,
        logger=logger,
        suppress_lifecycle_exit_statuses=False,
    )
    if not _is_enabled(runner, owner.mode_name) or owner.validate_fn is None:
        return None
    return owner.validate_fn(owner.ctx, preparation=preparation)


def _dispatch_owner(owner: DirectExecutionOwner) -> ExecutionResult:
    return dispatch_execution_call(
        owner.ctx,
        mode_name=owner.mode_name,
        execute=owner.execute_fn,
    )


def invoke_decision_direct(
    runner: Any,
    *,
    state: Any,
    decision: Any,
    user_input: str | None,
    logger: Any,
    depth: int = 0,
) -> ExecutionResult:
    owner = _direct_owner(
        runner=runner,
        state=state,
        decision=decision,
        user_input=user_input,
        logger=logger,
        suppress_lifecycle_exit_statuses=depth > 0,
    )
    if not _is_enabled(runner, owner.mode_name):
        raise ValueError(f"Mode is disabled: {owner.mode_name!r}")
    _enforce_depth_limit(runner, mode_name=owner.mode_name, depth=depth)
    return _dispatch_owner(owner)


def maybe_resume_task_backed_direct(
    runner: Any,
    *,
    state: Any,
    user_input: str | None,
    logger: Any,
    preferred_task_id: str | None = None,
) -> ExecutionResult | None:
    if state.plan is not None and 0 <= int(getattr(state, "cursor", 0) or 0) < len(
        state.plan.steps
    ):
        return None
    if str(user_input or "").strip() and not is_resume_like_input(user_input):
        return None
    manager = getattr(runner, "task_manager", None)
    if manager is None:
        return None
    normalized_preferred_task_id = str(
        preferred_task_id or getattr(state, "resume_task_id_hint", "") or ""
    ).strip()
    records: list[Any]
    if normalized_preferred_task_id:
        preferred_record = manager.get_task(normalized_preferred_task_id)
        if preferred_record is None:
            return None
        current_state = (
            str(getattr(preferred_record.state, "value", preferred_record.state))
            .strip()
            .lower()
        )
        if current_state not in {"active", "paused"}:
            return None
        records = [preferred_record]
    else:
        records = manager.list_open_tasks_for_session(state.session_id, limit=25)
    from ..checkpoint.contracts import TaskBackedModeContract  # noqa: PLC0415

    for record in records:
        mode_name = str(record.metadata.get("mode_name", "") or "").strip()
        if not mode_name:
            continue
        try:
            handler = _configure_handler(
                runner,
                _internal_handler_for_mode(mode_name),
                mode_name=mode_name,
            )
        except ValueError:
            continue
        if not bool(getattr(handler, "has_resume", False)):
            continue
        if not isinstance(handler, TaskBackedModeContract):
            continue
        checkpoint = manager.get_latest_checkpoint(record.task_id)
        checkpoint_id = checkpoint[0] if checkpoint is not None else ""
        state.task_backed_task_id = record.task_id
        state.task_backed_checkpoint_id = checkpoint_id or None
        state.goal = (
            str(record.metadata.get("goal") or state.goal or "").strip() or state.goal
        )
        decision = SimpleNamespace(
            route=mode_name,
            confidence=1.0,
            reason_code="resume_task_backed_mode",
            objective=str(record.metadata.get("goal") or state.goal or ""),
            sub_intents=[],
            rationale="",
            question=None,
            answer=None,
        )
        ctx = build_execution_context(
            runner,
            state=state,
            decision=decision,
            user_input=user_input,
            logger=logger,
        )
        state.task_backed_resume_state = (
            handler.resume(ctx, checkpoint_id) if checkpoint_id else {}
        )
        return _dispatch_owner(
            DirectExecutionOwner(
                mode_name=mode_name,
                ctx=ctx,
                prepare_fn=getattr(handler, "prepare", None)
                if bool(getattr(handler, "has_prepare", False))
                else None,
                validate_fn=getattr(handler, "validate", None)
                if bool(getattr(handler, "has_validate", False))
                else None,
                execute_fn=getattr(handler, "execute"),
            )
        )
    return None


__all__ = [
    "DirectExecutionOwner",
    "invoke_decision_direct",
    "maybe_resume_task_backed_direct",
    "prepare_decision_direct",
    "validate_decision_direct",
]
