from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import TYPE_CHECKING
from openminion.modules.context.summary.engine import (
    DEFAULT_SESSION_SUMMARY_ENGINE,
    SummaryTurn,
)

from .schemas import (
    BudgetStopReason,
    BudgetCounters,
    BrainMode,
    ClarifyPolicy,
    StepOutput,
    WorkingState,
    iso_now,
)
from openminion.base.config.action_policy import (
    ACTION_POLICY_SESSION_OVERRIDE_KEY,
    normalize_action_policy_mode_override,
)
from .execution.mission import sync_mission_budget_progress
from .adapters.tool.permission_mode import (
    canonical_permission_mode,
    canonical_permission_overrides,
)
from .execution.decide_contract import is_internal_failure_reason_code
from .constants import (
    BRAIN_ACTION_STATUS_FAILURES,
    BRAIN_ACTION_STATUS_NEEDS_USER,
    BRAIN_ACTION_STATUS_RETRY,
    BRAIN_ACTION_STATUS_SUCCESS,
    BRAIN_STATE_DONE,
    BRAIN_STATE_ERROR,
    BRAIN_STATE_STOPPED,
    BRAIN_STATE_WAITING_USER,
    RESPOND_KIND_ASSISTANT,
    RESPOND_KIND_POLICY_CONFIRMATION_PROMPT,
    RESPOND_KIND_VALUES,
    SESSION_EVENT_POLICY_CONFIRMATION_PROMPT,
    RespondKindLiteral,
)
from .diagnostics.transitions import set_status_unchecked

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .diagnostics.events import CanonicalEventLogger
    from .runner import BrainRunner


@dataclass
class MetaApplication:
    tier_before: str
    tier_after: str
    constraints_added: list[str] = field(default_factory=list)
    budgets_adjusted: bool = False
    llm_calls_max_before: int = 0
    llm_calls_max_after: int = 0


def _derive_llm_calls_max(runner: "BrainRunner") -> int:
    ticks = max(1, int(getattr(runner.profile.budgets, "max_ticks_per_user_turn", 8)))
    return max(8, min(32, ticks))


_STRUCTURED_REPLAY_STATE_FIELDS = {
    "pending_confirmation_sub_intent_refs",
    "pending_confirmation_feasibility_state",
    "pending_confirmation_feasibility_report",
    "decision_sub_intent_refs",
    "decision_feasibility_state",
    "decision_feasibility_report",
    "intent_execution_states",
}


def _normalize_skill_id_list(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values:
        skill_id = str(raw or "").strip()
        if not skill_id:
            continue
        lowered = skill_id.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        normalized.append(skill_id)
    return normalized


def _state_payload_from_raw(raw: dict[str, object]) -> dict[str, object]:
    if "state_inline" in raw and isinstance(raw.get("state_inline"), dict):
        return dict(raw["state_inline"])
    return dict(raw)


def _needs_structured_replay_backfill(
    *,
    raw_state: dict[str, object],
    state: WorkingState,
) -> bool:
    missing_fields = any(
        field not in raw_state for field in _STRUCTURED_REPLAY_STATE_FIELDS
    )
    if not missing_fields:
        return False
    return any(
        bool(getattr(state, field, None)) for field in _STRUCTURED_REPLAY_STATE_FIELDS
    )


def load_or_init_state(runner: "BrainRunner", session_id: str) -> WorkingState:
    session_mode_override = _session_action_policy_mode_override(
        runner,
        session_id=session_id,
    )
    (
        session_skill_loaded,
        session_skill_unloaded,
        session_skill_mode,
        has_session_skill_meta,
    ) = _session_skill_override_state(
        runner,
        session_id=session_id,
    )
    raw = runner.session_api.get_latest_working_state(session_id)
    if raw:
        raw_state = _state_payload_from_raw(raw)
        state = WorkingState.model_validate(raw_state)
        state_changed = _apply_pending_permission_overrides(runner, state)
        if session_mode_override != getattr(
            state,
            ACTION_POLICY_SESSION_OVERRIDE_KEY,
            None,
        ):
            state.session_action_policy_mode_override = session_mode_override
            state_changed = True
        if has_session_skill_meta:
            if state.session_skill_loaded != session_skill_loaded:
                state.session_skill_loaded = list(session_skill_loaded)
                state_changed = True
            if state.session_skill_unloaded != session_skill_unloaded:
                state.session_skill_unloaded = list(session_skill_unloaded)
                state_changed = True
            if state.skill_selection_mode != session_skill_mode:
                state.skill_selection_mode = session_skill_mode
                state_changed = True
        if state_changed:
            save_state(runner, state)
        if _needs_structured_replay_backfill(raw_state=raw_state, state=state):
            save_state(runner, state)
        if (
            state.mode == BrainMode.COMMAND
            and state.policy == ClarifyPolicy.ALWAYS_ASK
            and runner.options.clarify_config.default_mode != BrainMode.COMMAND
            and runner.options.clarify_config.default_policy != ClarifyPolicy.ALWAYS_ASK
        ):
            state.mode = BrainMode(runner.options.clarify_config.default_mode)
            state.policy = ClarifyPolicy(runner.options.clarify_config.default_policy)
            state.unresolved_clarify_items = []
            state.clarify_responses = {}
            save_state(runner, state)
        return state
    state = WorkingState(
        session_id=session_id,
        agent_id=runner.profile.agent_id,
        llm_calls_max=_derive_llm_calls_max(runner),
        budgets_remaining=BudgetCounters(
            ticks=runner.profile.budgets.max_ticks_per_user_turn,
            tool_calls=runner.profile.budgets.max_tool_calls,
            a2a_calls=runner.profile.budgets.max_a2a_calls,
            tokens=runner.profile.budgets.max_total_llm_tokens,
            time_ms=runner.profile.budgets.max_elapsed_ms,
        ),
        mode=runner.options.clarify_config.default_mode,
        policy=runner.options.clarify_config.default_policy,
        session_action_policy_mode_override=session_mode_override,
        session_skill_loaded=list(session_skill_loaded),
        session_skill_unloaded=list(session_skill_unloaded),
        skill_selection_mode=session_skill_mode,
    )
    _apply_pending_permission_overrides(runner, state)
    save_state(runner, state)
    return state


def _apply_pending_permission_overrides(
    runner: "BrainRunner",
    state: WorkingState,
) -> bool:
    before_mode = str(getattr(state, "permission_mode", "") or "")
    before_overrides = dict(getattr(state, "permission_overrides", {}) or {})
    permission_mode = canonical_permission_mode(
        str(getattr(runner, "_pending_permission_mode", "default") or "default")
    )
    if permission_mode == "ask":
        state.permission_mode = "default"
    else:
        state.permission_mode = permission_mode
    state.permission_overrides = canonical_permission_overrides(
        getattr(runner, "_pending_permission_overrides", {})
    )
    return before_mode != str(
        getattr(state, "permission_mode", "") or ""
    ) or before_overrides != dict(getattr(state, "permission_overrides", {}) or {})


def _session_action_policy_mode_override(
    runner: "BrainRunner",
    *,
    session_id: str,
) -> str | None:
    pending = normalize_action_policy_mode_override(
        getattr(runner, "_pending_session_action_policy_mode_override", None)
    )
    if pending is not None:
        return pending
    store = getattr(getattr(runner, "session_api", None), "store", None)
    if store is None or not hasattr(store, "get_session"):
        return None
    try:
        session = store.get_session(session_id)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(session, dict):
        return None
    meta = session.get("meta", {})
    if not isinstance(meta, dict):
        return None
    return normalize_action_policy_mode_override(
        meta.get(ACTION_POLICY_SESSION_OVERRIDE_KEY)
    )


def _session_skill_override_state(
    runner: "BrainRunner",
    *,
    session_id: str,
) -> tuple[list[str], list[str], str | None, bool]:
    store = getattr(getattr(runner, "session_api", None), "store", None)
    if store is None or not hasattr(store, "get_session"):
        return [], [], None, False
    try:
        session = store.get_session(session_id)
    except Exception:  # noqa: BLE001
        return [], [], None, False
    if not isinstance(session, dict):
        return [], [], None, False
    meta = session.get("meta", {})
    if not isinstance(meta, dict):
        return [], [], None, False
    has_any = any(
        key in meta
        for key in (
            "session_skill_loaded",
            "session_skill_unloaded",
            "skill_selection_mode",
        )
    )
    return (
        _normalize_skill_id_list(meta.get("session_skill_loaded")),
        _normalize_skill_id_list(meta.get("session_skill_unloaded")),
        (str(meta.get("skill_selection_mode", "") or "").strip().lower() or None),
        has_any,
    )


def save_state(runner: "BrainRunner", state: WorkingState) -> None:
    sync_mission_budget_progress(state)
    runner.session_api.put_working_state(
        state.session_id, state_inline=state.model_dump(mode="json")
    )


def set_session_status(runner: "BrainRunner", session_id: str, status: str) -> None:
    try:
        runner.session_api.update_session_status(session_id, status)
    except Exception:  # noqa: BLE001
        return


def update_session_summary(
    runner: "BrainRunner", session_id: str, agent_id: str
) -> None:
    del agent_id
    try:
        if hasattr(runner.session_api, "update_summary"):
            turns = runner.session_api.list_turns(session_id)
            if turns:
                normalized_turns: list[tuple[str, str]] = []
                for turn in turns:
                    if not isinstance(turn, dict):
                        continue
                    role = (
                        str(turn.get("role", turn.get("turn_type", "?")))
                        .strip()
                        .lower()
                        or "?"
                    )
                    if role in {"inbound"}:
                        role = "user"
                    elif role in {"outbound"}:
                        role = "assistant"
                    if role not in {"user", "assistant"}:
                        continue
                    text = str(turn.get("content", turn.get("text", "")) or "").strip()
                    if not text:
                        continue
                    normalized_turns.append((role, text))
                if not normalized_turns:
                    return
                summary_turns = [
                    SummaryTurn(role=role, text=text) for role, text in normalized_turns
                ]
                summary_short = DEFAULT_SESSION_SUMMARY_ENGINE.render_summary_short(
                    summary_turns
                )
                summary_long = DEFAULT_SESSION_SUMMARY_ENGINE.render_summary_long(
                    summary_turns
                )
                runner.session_api.update_summary(
                    session_id=session_id,
                    summary_short=summary_short,
                    summary_long=summary_long,
                )
    except Exception:  # noqa: BLE001
        return


def consume_tick(state: WorkingState) -> BudgetStopReason | None:
    if state.budgets_remaining.ticks <= 0:
        return BudgetStopReason.TICKS_EXHAUSTED
    if state.budgets_remaining.time_ms <= 0:
        return BudgetStopReason.TIME_EXHAUSTED
    state.budgets_remaining.ticks -= 1
    return None


def stale_clarify_state_should_clear(
    state: WorkingState, *, user_input: str | None
) -> bool:
    if not str(user_input or "").strip():
        return False
    if not list(getattr(state, "unresolved_clarify_items", []) or []):
        return False
    return is_internal_failure_reason_code(
        str(getattr(state, "decision_reason_code", "") or "").strip()
    )


def clear_clarify_state(state: WorkingState) -> None:
    state.unresolved_clarify_items = []
    state.pending_clarify_items = []
    state.clarify_resume_cursor = None
    state.pending_llm_clarify_context = None


def _skill_used_for_from_phase(phase: str | None) -> str:
    normalized = str(phase or "").strip().upper()
    if normalized in {"ACT", "OBSERVE"}:
        return "act"
    if normalized in {"VERIFY", "REFLECT", "IMPROVE"}:
        return "verify"
    return "plan"


def _skill_outcome_from_result(status: str, action_result) -> str:
    normalized_status = str(status or "").strip().lower()
    if normalized_status in {BRAIN_STATE_ERROR, BRAIN_STATE_STOPPED}:
        return "fail"
    action_status = str(getattr(action_result, "status", "") or "").strip().lower()
    if action_status in BRAIN_ACTION_STATUS_FAILURES:
        return "fail"
    if action_status in {BRAIN_ACTION_STATUS_RETRY, BRAIN_ACTION_STATUS_NEEDS_USER}:
        return "partial"
    return BRAIN_ACTION_STATUS_SUCCESS


def _log_active_skill_run(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    logger: "CanonicalEventLogger",
    status: str,
    action_result=None,
) -> None:
    skill_id = str(getattr(state, "active_skill_id", "") or "").strip()
    version_hash = str(getattr(state, "active_skill_version_hash", "") or "").strip()
    if not skill_id or not version_hash:
        state.active_skill_ids = []
        state.active_skill_id = None
        state.active_skill_version_hash = None
        return

    used_for = _skill_used_for_from_phase(getattr(state, "phase", None))
    outcome = _skill_outcome_from_result(status, action_result)
    evidence_refs = [
        str(getattr(ref, "ref", "") or "").strip()
        for ref in list(getattr(action_result, "artifact_refs", []) or [])
        if str(getattr(ref, "ref", "") or "").strip()
    ]
    try:
        if runner.skill_api is None:
            return
        runner.skill_api.log_run(
            session_id=state.session_id,
            agent_id=state.agent_id,
            skill_id=skill_id,
            version_hash=version_hash,
            used_for=used_for,
            outcome=outcome,
            evidence_refs=evidence_refs or None,
        )
    except Exception as exc:  # noqa: BLE001
        logger.emit(
            "skill.log_run.failed",
            {"error": str(exc), "skill_id": skill_id, "version_hash": version_hash},
            trace_id=state.trace_id,
        )
    finally:
        state.active_skill_ids = []
        state.active_skill_id = None
        state.active_skill_version_hash = None


def respond_structural_noop(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    logger: "CanonicalEventLogger",
    status: str = BRAIN_STATE_DONE,
    action_result=None,
) -> StepOutput:
    """Structural no-op respond path for PAE idle-tick coerced no_op."""
    _log_active_skill_run(
        runner,
        state=state,
        logger=logger,
        status=status,
        action_result=action_result,
    )
    state.phase = "RESPOND"
    set_status_unchecked(state, status, reason="pae_idle_tick_noop")
    # Deliberately skip:
    set_session_status(runner, state.session_id, status)
    save_state(runner, state)
    logger.emit(
        "pae.idle_tick.noop",
        {"session_id": state.session_id},
        trace_id=state.trace_id,
        status="ok",
    )
    return StepOutput(
        session_id=state.session_id,
        status=state.status,
        message="",  # empty — no sentinel leaks into AgentResponse.text
        working_state=state,
        action_result=action_result,
        # Explicit structural marker for downstream layers. More
        pae_idle_tick_noop=True,
    )


def respond(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    logger: "CanonicalEventLogger",
    message: str,
    status: str,
    action_result=None,
    kind: RespondKindLiteral = RESPOND_KIND_ASSISTANT,
) -> StepOutput:
    if kind not in RESPOND_KIND_VALUES:
        raise ValueError(f"unknown respond kind: {kind!r}")
    _log_active_skill_run(
        runner,
        state=state,
        logger=logger,
        status=status,
        action_result=action_result,
    )
    preserve_clarify_phase = (
        str(status).strip().lower() == BRAIN_STATE_WAITING_USER
        and str(getattr(state, "phase", "")).strip().upper() == "CLARIFY"
    )
    if not preserve_clarify_phase:
        state.phase = "RESPOND"
    set_status_unchecked(state, status, reason="respond_passthrough")
    emit_phase_status = getattr(runner, "_emit_phase_status", None)
    if callable(emit_phase_status):
        emit_phase_status(
            state=state,
            runtime_status=str(state.status),
            detail_text=message,
        )
    if kind == RESPOND_KIND_POLICY_CONFIRMATION_PROMPT:
        append_event = getattr(runner.session_api, "append_event", None)
        if callable(append_event):
            append_event(
                state.session_id,
                SESSION_EVENT_POLICY_CONFIRMATION_PROMPT,
                {
                    "ts": iso_now(),
                    "status": status,
                    "message": message,
                    "is_error": bool(getattr(action_result, "error", None)),
                },
            )
    else:
        runner.session_api.append_turn(
            state.session_id,
            "assistant",
            message,
            meta={
                "ts": iso_now(),
                "status": status,
                "is_error": bool(getattr(action_result, "error", None)),
            },
        )
    set_session_status(runner, state.session_id, status)
    update_session_summary(runner, session_id=state.session_id, agent_id=state.agent_id)
    if kind == RESPOND_KIND_ASSISTANT:
        runner._compact(state=state, logger=logger, content=message)
    save_state(runner, state)
    return StepOutput(
        session_id=state.session_id,
        status=state.status,
        message=message,
        working_state=state,
        action_result=action_result,
        kind=kind,
    )


def compact(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    logger: "CanonicalEventLogger",
    content: str = "",
) -> None:
    if runner.context_api is None:
        return
    delta = runner.context_api.make_delta(
        session_id=state.session_id,
        agent_id=state.agent_id,
        content=str(content or ""),
    )
    delta_ref = (
        delta
        if isinstance(delta, str)
        else str(delta.get("ref") or json.dumps(delta, ensure_ascii=True))
    )
    logger.emit(
        "summary.updated",
        {"delta_ref": delta_ref},
        trace_id=state.trace_id,
        artifact_refs=[delta_ref],
    )
    runner.context_api.maybe_compact(
        session_id=state.session_id, agent_id=state.agent_id
    )
