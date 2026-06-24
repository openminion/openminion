from __future__ import annotations

from openminion.modules.context.contracts import (
    BRAIN_CONTRACT_VERSION,
    CONTEXT_CONTRACT_VERSION,
    MEMORY_CONTRACT_VERSION,
    SESSION_CONTRACT_VERSION,
)

POST_RESET_BASELINE_VERSION: str = "v1"

CONTRACT_REGISTRY: dict[str, str] = {
    "context": CONTEXT_CONTRACT_VERSION,
    "session": SESSION_CONTRACT_VERSION,
    "memory": MEMORY_CONTRACT_VERSION,
    "brain": BRAIN_CONTRACT_VERSION,
}


def check_contract_registry_health(
    registry: dict[str, str] | None = None,
    *,
    expected_version: str = POST_RESET_BASELINE_VERSION,
) -> dict[str, object]:
    """Inspect contract version alignment across registered domains."""

    inspected = dict(CONTRACT_REGISTRY if registry is None else registry)
    mismatches: dict[str, str] = {
        name: version
        for name, version in inspected.items()
        if version != expected_version
    }
    return {
        "aligned": not mismatches,
        "expected_version": expected_version,
        "registry": inspected,
        "mismatches": mismatches,
    }


__all__ = [
    "CONTRACT_REGISTRY",
    "POST_RESET_BASELINE_VERSION",
    "check_contract_registry_health",
]
