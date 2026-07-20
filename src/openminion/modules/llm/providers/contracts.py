import warnings
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from openminion.base.config.env import resolve_environment_config
from openminion.base.constants import OPENMINION_STRICT_PROVIDER_RESPONSE_CONTRACTS_ENV
from openminion.modules.llm.constants import (
    LLM_TOOL_CALL_STRATEGY_HYBRID,
    LLM_TOOL_CHOICE_AUTO,
)
from openminion.modules.tool.contracts import (
    ProviderToolCall,
    ProviderToolSpec,
)

PROVIDER_RESPONSE_INTERFACE_VERSION = "v1"


@dataclass(frozen=True)
class ThinkingBlock:
    """Reasoning block emitted by a thinking-capable provider."""

    type: Literal["thinking"] = "thinking"
    content: str = ""
    signature: str | None = None
    redacted: bool = False


STRICT_PROVIDER_RESPONSE_CONTRACTS_ENV = (
    OPENMINION_STRICT_PROVIDER_RESPONSE_CONTRACTS_ENV
)


class ProviderError(RuntimeError):
    """Raised when a provider cannot complete a request."""


@dataclass
class ProviderRequest:
    user_message: str
    system_prompt: str
    thinking: str = "minimal"
    history: list["ProviderHistoryMessage"] = field(default_factory=list)
    tools: list[ProviderToolSpec] = field(default_factory=list)
    tool_choice: str | dict[str, Any] = LLM_TOOL_CHOICE_AUTO
    tool_call_strategy: str = LLM_TOOL_CALL_STRATEGY_HYBRID
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass
class ProviderHistoryMessage:
    role: str
    content: str
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def body(self) -> str:
        return self.content

    @body.setter
    def body(self, value: str) -> None:
        self.content = value


@dataclass
class ProviderResponse:
    text: str
    model: str
    usage: dict[str, Any] = field(default_factory=dict)
    tool_calls: list[ProviderToolCall] = field(default_factory=list)
    finish_reason: str = ""
    normalization: dict[str, Any] = field(default_factory=dict)
    thinking: list[ThinkingBlock] = field(default_factory=list)


@runtime_checkable
class ProviderResponseCompatible(Protocol):
    contract_version: str


def provider_response_contracts_strict(*, default: bool = False) -> bool:
    raw = (
        str(
            resolve_environment_config().get(STRICT_PROVIDER_RESPONSE_CONTRACTS_ENV, "")
        )
        .strip()
        .lower()
    )
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "on"}


def validate_provider_response_shape(method_name: str, value: Any) -> None:
    """Validate the return shape for a Layer-2 provider method."""
    method = str(method_name or "").strip()
    if method == "list_models":
        if not isinstance(value, list) or not all(
            isinstance(item, str) for item in value
        ):
            raise ProviderError(
                f"list_models must return list[str]; got {type(value).__name__}"
            )
        return
    if method == "healthcheck":
        if not isinstance(value, dict):
            raise ProviderError(
                f"healthcheck must return dict; got {type(value).__name__}"
            )
        return
    if method == "complete":
        # The runtime accepts either an LLMResponse or a ProviderAdapterResult
        for attr in ("provider", "model"):
            if not hasattr(value, attr):
                raise ProviderError(
                    f"complete must return LLMResponse-compatible object; "
                    f"missing attribute {attr!r}"
                )
        return
    raise ProviderError(f"Unknown provider method for shape validation: {method!r}")


def ensure_provider_response_compatibility(
    component: Any,
    *,
    component_name: str = "provider",
    expected_version: str = PROVIDER_RESPONSE_INTERFACE_VERSION,
    strict: bool | None = None,
) -> bool:
    strict_mode = (
        provider_response_contracts_strict() if strict is None else bool(strict)
    )
    actual = getattr(component, "contract_version", None)
    if isinstance(actual, str) and actual.strip() == expected_version:
        return True

    message = (
        f"Provider response contract mismatch for {component_name}: "
        f"expected={expected_version!r} actual={actual!r}"
    )
    if strict_mode:
        raise ProviderError(message)
    warnings.warn(message, RuntimeWarning, stacklevel=2)
    return False
