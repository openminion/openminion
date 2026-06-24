from abc import ABC, abstractmethod
from .contracts import (
    PROVIDER_RESPONSE_INTERFACE_VERSION,
    ProviderError,
    ProviderHistoryMessage,
    ProviderRequest,
    ProviderResponse,
    ProviderToolCall,
    ProviderToolSpec,
    ensure_provider_response_compatibility,
    provider_response_contracts_strict,
)


class LLMProvider(ABC):
    name = "provider"
    contract_version = PROVIDER_RESPONSE_INTERFACE_VERSION

    @abstractmethod
    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        """Generate a model response for the given request."""


__all__ = [
    "LLMProvider",
    "PROVIDER_RESPONSE_INTERFACE_VERSION",
    "ProviderError",
    "ProviderHistoryMessage",
    "ProviderRequest",
    "ProviderResponse",
    "ProviderToolCall",
    "ProviderToolSpec",
    "ensure_provider_response_compatibility",
    "provider_response_contracts_strict",
]
