from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from .schemas import UsageInfo

ErrorCode = Literal[
    "INVALID_ARGUMENT",
    "AUTH_ERROR",
    "RATE_LIMITED",
    "TIMEOUT",
    "PROVIDER_ERROR",
    "POLICY_DENIED",
    "INTERNAL_ERROR",
    # Cortensor empty payload error codes
    "EMPTY_PAYLOAD",
    "EMPTY_URN_CONTENT",
    "MALFORMED_PAYLOAD",
]


class LLMCtlError(Exception):
    """Typed runtime error mapped to canonical LLMResponse.error."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        details: dict[str, Any] | None = None,
        *,
        usage: "UsageInfo | None" = None,
    ) -> None:
        super().__init__(message)
        self.code: ErrorCode = code
        self.message = message
        self.details = dict(details or {})
        self.usage = usage
