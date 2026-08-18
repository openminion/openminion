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

_PROVIDER_FALLBACK = "is_provider_recovery_fallback_text"
_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "LLMCTL": (".runtime.client", "LLMCTL"),
    "LLMClient": (".runtime.client", "LLMClient"),
    "LLMCtlError": (".errors", "LLMCtlError"),
    "ErrorCode": (".errors", "ErrorCode"),
    "Message": (".schemas", "Message"),
    "ProviderError": (".providers.contracts", "ProviderError"),
    "ToolChoice": (".schemas", "ToolChoice"),
    "ToolSpec": (".schemas", "ToolSpec"),
    "ToolCall": (".schemas", "ToolCall"),
    "UsageInfo": (".schemas", "UsageInfo"),
    "LLMRequest": (".schemas", "LLMRequest"),
    "LLMResponse": (".schemas", "LLMResponse"),
    "ResponseError": (".schemas", "ResponseError"),
    "RuntimeLLMHandle": (".providers.factory", "RuntimeLLMHandle"),
    _PROVIDER_FALLBACK: (".providers.normalization", _PROVIDER_FALLBACK),
}


def __getattr__(name: str) -> Any:  # pragma: no cover
    target = _LAZY_EXPORTS.get(name)
    if not target:
        raise AttributeError(name)
    module_name, attr_name = target
    module = __import__(__name__ + module_name, fromlist=[attr_name])
    value = getattr(module, attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:  # pragma: no cover
    return sorted(set(list(globals().keys()) + list(_LAZY_EXPORTS.keys())))
