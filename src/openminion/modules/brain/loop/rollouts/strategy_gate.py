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
    kind = step_kind.strip()
    if kind not in PARALLEL_ROLLOUT_ELIGIBLE_STEP_KINDS:
        return False
    if operator_allowlist is None:
        return True
    return kind in {item.strip() for item in operator_allowlist}
