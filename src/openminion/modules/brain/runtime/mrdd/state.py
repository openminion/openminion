"""Helpers for MRDD state persisted on `WorkingState.module_state`."""

from typing import Any, Mapping

from openminion.modules.brain.constants import STATE_KEY_MODULE_STATE
from openminion.modules.brain.runtime.regrounding import RegroundingPolicy


_MRDD_MODULE_STATE_KEY = "mrdd"
_CADENCE_COUNTER_KEY = "cadence_counter"
_LAST_INJECT_AT_KEY = "last_inject_at"
_LAST_SIGNAL_AT_KEY = "last_signal_at"
_POLICY_KEY = "policy"


def get_mrdd_module_state(working_state: Any) -> dict[str, Any]:
    """Return the MRDD module-state bucket, creating it when needed."""

    module_state = getattr(working_state, STATE_KEY_MODULE_STATE, None)
    if not isinstance(module_state, dict):
        return {}
    bucket = module_state.get(_MRDD_MODULE_STATE_KEY)
    if not isinstance(bucket, dict):
        bucket = {}
        module_state[_MRDD_MODULE_STATE_KEY] = bucket
    return bucket


def read_cadence_counter(working_state: Any) -> int:
    """Return the persisted cadence counter."""

    bucket = get_mrdd_module_state(working_state)
    raw = bucket.get(_CADENCE_COUNTER_KEY, 0)
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


def write_cadence_counter(working_state: Any, value: int) -> None:
    """Persist the cadence counter for the next tick."""

    bucket = get_mrdd_module_state(working_state)
    bucket[_CADENCE_COUNTER_KEY] = max(0, int(value))


def stamp_last_inject(working_state: Any, *, at: str, goal_id: str) -> None:
    """Stamp the most recent inject timestamp and goal id."""

    bucket = get_mrdd_module_state(working_state)
    bucket[_LAST_INJECT_AT_KEY] = {
        "at": str(at or "").strip(),
        "goal_id": str(goal_id or "").strip(),
    }


def stamp_last_signal(
    working_state: Any, *, at: str, kind: str, signal_id: str
) -> None:
    """Stamp the most recent drift signal."""

    bucket = get_mrdd_module_state(working_state)
    bucket[_LAST_SIGNAL_AT_KEY] = {
        "at": str(at or "").strip(),
        "kind": str(kind or "").strip(),
        "signal_id": str(signal_id or "").strip(),
    }


def read_policy_snapshot(working_state: Any) -> RegroundingPolicy:
    """Return the persisted policy snapshot or a default-disabled policy."""

    bucket = get_mrdd_module_state(working_state)
    raw = bucket.get(_POLICY_KEY)
    if not isinstance(raw, Mapping):
        return RegroundingPolicy()
    try:
        cadence = max(1, int(raw.get("cadence_turns", 10)))
    except (TypeError, ValueError):
        cadence = 10
    enabled = bool(raw.get("enabled", False))
    inject_after_compaction = bool(raw.get("inject_after_compaction", True))
    return RegroundingPolicy(
        cadence_turns=cadence,
        enabled=enabled,
        inject_after_compaction=inject_after_compaction,
    )


def write_policy_snapshot(working_state: Any, policy: RegroundingPolicy) -> None:
    """Persist the operator opt-in policy snapshot."""

    bucket = get_mrdd_module_state(working_state)
    bucket[_POLICY_KEY] = {
        "cadence_turns": int(policy.cadence_turns),
        "enabled": bool(policy.enabled),
        "inject_after_compaction": bool(policy.inject_after_compaction),
    }


__all__ = [
    "get_mrdd_module_state",
    "read_cadence_counter",
    "read_policy_snapshot",
    "stamp_last_inject",
    "stamp_last_signal",
    "write_cadence_counter",
    "write_policy_snapshot",
]
