"""Canonical error types for the v2 tool-call envelope."""

from __future__ import annotations

from typing import Any, Mapping


class EnvelopeError(Exception):
    code: str = ""

    def __init__(
        self,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message: str = message
        self.details: dict[str, Any] = dict(details or {})

    def to_envelope(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": dict(self.details),
        }


class InvalidEnvelopeShapeError(EnvelopeError):
    code = "INVALID_ENVELOPE_SHAPE"


class InvalidEnvelopeVersionError(EnvelopeError):
    code = "INVALID_ENVELOPE_VERSION"


class InvalidCallShapeError(EnvelopeError):
    code = "INVALID_CALL_SHAPE"


class InvalidResultShapeError(EnvelopeError):
    code = "INVALID_RESULT_SHAPE"


class DuplicateCallIdError(EnvelopeError):
    code = "DUPLICATE_CALL_ID"


class UnknownToolNameError(EnvelopeError):
    code = "UNKNOWN_TOOL_NAME"


class InvalidToolArgumentsError(EnvelopeError):
    code = "INVALID_TOOL_ARGUMENTS"


class UnknownDependencyError(EnvelopeError):
    code = "UNKNOWN_DEPENDENCY"


class DependencyCycleError(EnvelopeError):
    code = "DEPENDENCY_CYCLE"


class DependencyFailedError(EnvelopeError):
    code = "DEPENDENCY_FAILED"


ENVELOPE_ERROR_CODES: frozenset[str] = frozenset(
    {
        "INVALID_ENVELOPE_SHAPE",
        "INVALID_ENVELOPE_VERSION",
        "INVALID_CALL_SHAPE",
        "INVALID_RESULT_SHAPE",
        "DUPLICATE_CALL_ID",
        "UNKNOWN_TOOL_NAME",
        "INVALID_TOOL_ARGUMENTS",
        "UNKNOWN_DEPENDENCY",
        "DEPENDENCY_CYCLE",
        "DEPENDENCY_FAILED",
    }
)


__all__ = [
    "EnvelopeError",
    "InvalidEnvelopeShapeError",
    "InvalidEnvelopeVersionError",
    "InvalidCallShapeError",
    "InvalidResultShapeError",
    "DuplicateCallIdError",
    "UnknownToolNameError",
    "InvalidToolArgumentsError",
    "UnknownDependencyError",
    "DependencyCycleError",
    "DependencyFailedError",
    "ENVELOPE_ERROR_CODES",
]
