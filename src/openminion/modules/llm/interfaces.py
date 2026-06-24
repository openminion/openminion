import warnings
from typing import Any, Protocol, runtime_checkable

from openminion.base.config.env import resolve_environment_config

from .errors import LLMCtlError

LLM_RESPONSE_INTERFACE_VERSION = "v1"
PROVIDER_INTERFACE_VERSION = "v1"
STRICT_LLM_RESPONSE_CONTRACTS_ENV = "OPENMINION_STRICT_LLM_RESPONSE_CONTRACTS"


@runtime_checkable
class LLMResponseCompatible(Protocol):
    contract_version: str


def llm_response_contracts_strict(*, default: bool = False) -> bool:
    raw = (
        resolve_environment_config()
        .get(STRICT_LLM_RESPONSE_CONTRACTS_ENV, "")
        .strip()
        .lower()
    )
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "on"}


def ensure_llm_response_compatibility(
    component: Any,
    *,
    component_name: str = "component",
    expected_version: str = LLM_RESPONSE_INTERFACE_VERSION,
    strict: bool | None = None,
) -> bool:
    strict_mode = llm_response_contracts_strict() if strict is None else bool(strict)
    actual = getattr(component, "contract_version", None)
    if isinstance(actual, str) and actual.strip() == expected_version:
        return True

    details = {
        "component": component_name,
        "expected_contract_version": expected_version,
        "actual_contract_version": actual,
    }
    message = (
        f"LLM response contract mismatch for {component_name}: "
        f"expected={expected_version!r} actual={actual!r}"
    )

    if strict_mode:
        raise LLMCtlError("INTERNAL_ERROR", message, details)

    warnings.warn(message, RuntimeWarning, stacklevel=2)
    return False
