"""Allowlist gate for parallel-eligible step kinds."""

from typing import Iterable


PARALLEL_ROLLOUT_ELIGIBLE_STEP_KINDS: frozenset[str] = frozenset(
    {
        "patch_apply",
        "structured_json_emit",
        "test_authoring",
    }
)


def is_step_eligible_for_parallel_rollout(
    step_kind: str,
    *,
    operator_allowlist: Iterable[str] | None = None,
) -> bool:
    """Return whether a step kind is eligible for parallel rollout."""

    kind = str(step_kind or "").strip()
    if kind not in PARALLEL_ROLLOUT_ELIGIBLE_STEP_KINDS:
        return False
    if operator_allowlist is None:
        return True
    return kind in {str(k or "").strip() for k in operator_allowlist if k}
