from typing import Any

from .contracts.schemas import ErrorCode


class ToolRuntimeError(Exception):
    """Typed runtime error mapped to the standard envelope error shape."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code: ErrorCode = code
        self.message = message
        self.details = details or {}
