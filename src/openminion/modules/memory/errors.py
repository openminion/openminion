from abc import ABCMeta
from typing import Any, ClassVar

from sophiagraph.contracts.errors import (
    InvalidArgumentError as _SophiagraphInvalidArgumentError,
)


class _MemctlMeta(ABCMeta):
    """Metaclass for the memctl error hierarchy."""


class MemctlError(RuntimeError, metaclass=_MemctlMeta):
    """Base error carrying a stable code and optional details."""

    code: ClassVar[str] = "UNKNOWN"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


class InvalidArgumentError(MemctlError):
    """Invalid argument contract."""

    code = "INVALID_ARGUMENT"


InvalidArgumentError.register(_SophiagraphInvalidArgumentError)


class NotFoundError(MemctlError):
    code = "NOT_FOUND"


class ConstraintViolationError(MemctlError):
    code = "CONSTRAINT_VIOLATION"


class PromotionDeniedError(MemctlError):
    code = "PROMOTION_DENIED"


class StoreReadError(MemctlError):
    code = "STORE_READ_FAILED"


class StoreWriteError(MemctlError):
    code = "STORE_WRITE_FAILED"


class MemoryQueryUnavailableError(MemctlError):
    code = "MEMORY_UNAVAILABLE"


class MigrationRequiredError(MemctlError):
    code = "MIGRATION_REQUIRED"
