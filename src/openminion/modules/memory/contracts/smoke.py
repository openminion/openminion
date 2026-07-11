from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SmokeMemoryContractCheck:
    ok: bool
    errors: list[str]


_REQUIRED_METHODS = (
    "build_context",
    "build_retrieval_context",
    "record_turn",
)


def ensure_memory_smoke_contract(
    component: Any,
    *,
    strict: bool = True,
) -> SmokeMemoryContractCheck:
    errors: list[str] = []
    for name in _REQUIRED_METHODS:
        member = getattr(component, name, None)
        if callable(member):
            continue
        issue = "missing" if member is None else "non-callable"
        errors.append(f"{issue} member: {name}")
    if errors and strict:
        raise TypeError(
            f"memory smoke contract violation: {errors}"
        )  # allow-bare-raise: defensive type guard
    return SmokeMemoryContractCheck(ok=not errors, errors=errors)


__all__ = [
    "SmokeMemoryContractCheck",
    "ensure_memory_smoke_contract",
]
