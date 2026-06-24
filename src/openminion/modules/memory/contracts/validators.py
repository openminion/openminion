from typing import Any

from ..errors import InvalidArgumentError
from .types import MEMORY_CONTRACT_VERSION


_ROLE_REQUIRED_MEMBERS: dict[str, tuple[str, ...]] = {
    "read": (
        "contract_version",
        "search",
        "retrieve_by_entities",
    ),
    "write": (
        "contract_version",
        "write_record",
    ),
    "candidate": (
        "contract_version",
        "stage_candidate",
        "review_candidate",
        "promote_candidate",
    ),
    "procedure": (
        "contract_version",
        "get_procedure",
    ),
    "introspection": (
        "contract_version",
        "get_runtime_snapshot",
    ),
    "capsule": (
        "contract_version",
        "build_capsule",
        "refresh_capsule",
    ),
    "service": (
        "contract_version",
        "set_vector_adapter",
        "get",
        "list",
        "search",
        "search_semantic",
        "candidate_put",
        "candidate_get",
        "candidate_list",
        "candidate_update",
        "promote_candidate",
    ),
    "backend": (
        "contract_version",
        "put_record",
        "upsert_record",
        "get_record",
        "list_records",
        "search_records",
        "invalidate_record",
        "supersede_record",
        "put_relation",
        "list_relations",
        "get_related_records",
        "put_candidate",
        "get_candidate",
        "list_candidates",
        "update_candidate",
        "promote_candidate",
        "list_tier_transitions",
        "put_tier_transition",
        "history",
        "export_snapshot",
        "import_snapshot",
    ),
}


class MemoryContractError(RuntimeError):
    """Raised when an implementation drifts from required memory contract shape."""

    def __init__(self, *, role: str, errors: list[str]) -> None:
        self.role = str(role or "")
        self.errors = list(errors)
        super().__init__(
            f"memory contract violation role={self.role}: " + "; ".join(self.errors)
        )


def ensure_memory_contract_compatibility(
    component: Any,
    *,
    role: str,
    strict: bool = True,
) -> tuple[bool, list[str]]:
    normalized_role = str(role or "").strip().lower()
    required = _ROLE_REQUIRED_MEMBERS.get(normalized_role)
    if required is None:
        raise InvalidArgumentError(f"unknown memory contract role: {role}")

    errors: list[str] = []
    for name in required:
        value = getattr(component, name, ...)
        if value is ...:
            errors.append(f"missing member: {name}")
            continue
        if name != "contract_version" and not callable(value):
            errors.append(f"non-callable member: {name}")

    declared_version = str(getattr(component, "contract_version", "")).strip()
    if declared_version != MEMORY_CONTRACT_VERSION:
        errors.append(
            "version mismatch: expected "
            f"{MEMORY_CONTRACT_VERSION}, got {declared_version or '<missing>'}"
        )

    if errors:
        if strict:
            raise MemoryContractError(role=normalized_role, errors=errors)
        return False, errors
    return True, []
