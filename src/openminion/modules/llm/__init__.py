from typing import TYPE_CHECKING, Any

__all__ = [
    "ErrorCode",
    "LLMCTL",
    "LLMClient",
    "LLMCtlError",
    "LLMRequest",
    "LLMResponse",
    "Message",
    "ProviderError",
    "ResponseError",
    "RuntimeLLMHandle",
    "ToolCall",
    "ToolChoice",
    "ToolSpec",
    "UsageInfo",
    "is_provider_recovery_fallback_text",
]
if TYPE_CHECKING:  # pragma: no cover
    from .runtime.client import LLMCTL, LLMClient
    from .errors import ErrorCode, LLMCtlError
    from .providers.factory import RuntimeLLMHandle
    from .providers.contracts import ProviderError
    from .providers.normalization import is_provider_recovery_fallback_text
    from .schemas import (
        LLMRequest,
        LLMResponse,
        Message,
        ResponseError,
        ToolCall,
        ToolChoice,
        ToolSpec,
        UsageInfo,
    )

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "ErrorCode": (".errors", "ErrorCode"),
    "LLMCTL": (".runtime.client", "LLMCTL"),
    "LLMClient": (".runtime.client", "LLMClient"),
    "LLMCtlError": (".errors", "LLMCtlError"),
    "LLMRequest": (".schemas", "LLMRequest"),
    "LLMResponse": (".schemas", "LLMResponse"),
    "Message": (".schemas", "Message"),
    "ProviderError": (".providers.contracts", "ProviderError"),
    "ResponseError": (".schemas", "ResponseError"),
    "RuntimeLLMHandle": (".providers.factory", "RuntimeLLMHandle"),
    "ToolCall": (".schemas", "ToolCall"),
    "ToolChoice": (".schemas", "ToolChoice"),
    "ToolSpec": (".schemas", "ToolSpec"),
    "UsageInfo": (".schemas", "UsageInfo"),
    "is_provider_recovery_fallback_text": (
        ".providers.normalization",
        "is_provider_recovery_fallback_text",
    ),
}


def __getattr__(name: str) -> Any:  # pragma: no cover
    if name not in _LAZY_EXPORTS:
        raise AttributeError(name)
    module_name, attr_name = _LAZY_EXPORTS[name]
    module = __import__(__name__ + module_name, fromlist=[attr_name])
    globals()[name] = getattr(module, attr_name)
    return globals()[name]


def __dir__() -> list[str]:  # pragma: no cover
    return sorted(globals().keys() | _LAZY_EXPORTS.keys())
