"""Run the MRDD pre-dispatch hook when policy enables it."""

from __future__ import annotations

from typing import Any

from openminion.base.time import utc_now_iso

from .state import (
    get_mrdd_module_state,
    read_cadence_counter,
    read_policy_snapshot,
    stamp_last_inject,
    stamp_last_signal,
    write_cadence_counter,
)
from .tick import MRDDTickInputs, run_mrdd_tick
from ...schemas.goals import Goal
from openminion.modules.telemetry.events.catalog import (
    MRDD_DRIFT_SIGNAL,
    MRDD_HOOK_ERROR,
    MRDD_REGROUNDING_INJECT,
)


def maybe_run_mrdd_pre_dispatch_hook(
    *,
    runner: Any,
    state: Any,
    logger: Any,
) -> None:
    """Run the MRDD coordinator when policy enables it."""

    try:
        bucket = get_mrdd_module_state(state)
        policy = read_policy_snapshot(state)
        forced = bool(bucket.get("forced", False))
        if not policy.enabled and not forced:
            return

        goal = _resolve_active_goal(runner=runner, state=state)
        if goal is None:
            return

        just_compacted = bool(bucket.get("just_compacted", False))
        cadence_counter = read_cadence_counter(state)
        detected_at = utc_now_iso()
        signal_id = f"mrdd-{goal.goal_id}-{detected_at}"

        outcome = run_mrdd_tick(
            MRDDTickInputs(
                goal=goal,
                policy=policy,
                cadence_counter=cadence_counter,
                just_compacted=just_compacted,
                trajectory=None,
                detected_at=detected_at,
                signal_id=signal_id,
                forced_regrounding=forced,
            )
        )

        write_cadence_counter(state, outcome.next_counter)
        if outcome.inject is not None:
            stamp_last_inject(state, at=detected_at, goal_id=goal.goal_id)
            _record_inject_event(
                logger=logger, inject=outcome.inject, detected_at=detected_at
            )
        if outcome.signal is not None:
            stamp_last_signal(
                state,
                at=detected_at,
                kind=outcome.signal.kind,
                signal_id=outcome.signal.signal_id,
            )
            _record_signal_event(
                logger=logger, signal=outcome.signal, detected_at=detected_at
            )
            _persist_signal_to_audit_trail(runner=runner, signal=outcome.signal)
        if just_compacted:
            bucket["just_compacted"] = False
        if forced:
            bucket["forced"] = False
    except Exception as exc:  # noqa: BLE001 - hook must never bubble
        try:
            logger.log_canonical_event(
                event_type=MRDD_HOOK_ERROR,
                payload={"error": str(exc)},
            )
        except Exception:
            pass  # logger failure is non-fatal too


def _resolve_active_goal(*, runner: Any, state: Any) -> Goal | None:
    """Resolve the active typed goal for the current session, if any."""

    bucket = get_mrdd_module_state(state)
    raw = bucket.get("active_goal_payload")
    if isinstance(raw, dict):
        try:
            return Goal(**raw)
        except Exception:
            pass

    long_running_goals = getattr(runner, "long_running_goals", None)
    if long_running_goals is not None:
        try:
            session_id = getattr(state, "session_id", "")
            active = long_running_goals.list_active_goals_for_session(session_id)
            if active:
                first = active[0]
                if isinstance(first, Goal):
                    return first
        except Exception:
            return None
    return None


def _record_inject_event(*, logger: Any, inject: Any, detected_at: str) -> None:
    try:
        logger.log_canonical_event(
            event_type=MRDD_REGROUNDING_INJECT,
            payload={
                "goal_id": inject.goal_id,
                "trigger_kind": inject.trigger.kind,
                "detected_at": detected_at,
            },
        )
    except Exception:
        pass


def _record_signal_event(*, logger: Any, signal: Any, detected_at: str) -> None:
    try:
        logger.log_canonical_event(
            event_type=MRDD_DRIFT_SIGNAL,
            payload={
                "signal_id": signal.signal_id,
                "goal_id": signal.goal_id,
                "kind": signal.kind,
                "detected_at": detected_at,
            },
        )
    except Exception:
        pass


def _persist_signal_to_audit_trail(*, runner: Any, signal: Any) -> None:
    """Persist the drift signal into the audit trail when available."""

    goal_store = getattr(runner, "goal_store", None)
    if goal_store is None:
        return
    record = getattr(goal_store, "record_drift_signal_audit", None)
    if record is None:
        return
    try:
        record(signal)
    except Exception:
        pass


__all__ = ["maybe_run_mrdd_pre_dispatch_hook"]
