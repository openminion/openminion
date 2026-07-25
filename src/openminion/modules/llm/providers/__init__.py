from typing import Any

from .base import (
    LLMProvider as LLMProvider,
    PROVIDER_RESPONSE_INTERFACE_VERSION as PROVIDER_RESPONSE_INTERFACE_VERSION,
    ProviderError as ProviderError,
    ProviderHistoryMessage as ProviderHistoryMessage,
    ProviderRequest as ProviderRequest,
    ProviderResponse as ProviderResponse,
    ProviderToolCall as ProviderToolCall,
    ProviderToolSpec as ProviderToolSpec,
    ensure_provider_response_compatibility as ensure_provider_response_compatibility,
    provider_response_contracts_strict as provider_response_contracts_strict,
)
from .exports import LAZY_EXPORTS, PUBLIC_EXPORTS, resolve_lazy_export

__all__ = PUBLIC_EXPORTS


def __getattr__(name: str) -> Any:  # pragma: no cover
    value = resolve_lazy_export(package_name=__name__, name=name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:  # pragma: no cover
    return sorted(set(list(globals().keys()) + list(LAZY_EXPORTS.keys())))
