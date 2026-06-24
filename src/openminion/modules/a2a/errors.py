from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

ERROR_CODE_INVALID_ARGUMENT = "INVALID_ARGUMENT"
ERROR_CODE_INVALID_CONFIG = "INVALID_CONFIG"
ERROR_CODE_POLICY_DENIED = "POLICY_DENIED"
ERROR_CODE_ALREADY_COMPLETED = "ALREADY_COMPLETED"
ERROR_CODE_IN_PROGRESS = "IN_PROGRESS"
ERROR_CODE_JOB_NOT_FOUND = "JOB_NOT_FOUND"
ERROR_CODE_NO_HANDLER = "NO_HANDLER"
ERROR_CODE_AGENT_NOT_FOUND = "AGENT_NOT_FOUND"
ERROR_CODE_ROUTE_NOT_FOUND = "ROUTE_NOT_FOUND"
ERROR_CODE_HANDLER_ERROR = "HANDLER_ERROR"
ERROR_CODE_JOB_FAILED = "JOB_FAILED"
ERROR_CODE_CANCELED = "CANCELED"
ERROR_CODE_STALE_JOB = "STALE_JOB"
ERROR_CODE_FAILED = "FAILED"
ERROR_CODE_INTERNAL_ERROR = "INTERNAL_ERROR"

ERROR_CODES: frozenset[str] = frozenset(
    {
        ERROR_CODE_INVALID_ARGUMENT,
        ERROR_CODE_INVALID_CONFIG,
        ERROR_CODE_POLICY_DENIED,
        ERROR_CODE_ALREADY_COMPLETED,
        ERROR_CODE_IN_PROGRESS,
        ERROR_CODE_JOB_NOT_FOUND,
        ERROR_CODE_NO_HANDLER,
        ERROR_CODE_AGENT_NOT_FOUND,
        ERROR_CODE_ROUTE_NOT_FOUND,
        ERROR_CODE_HANDLER_ERROR,
        ERROR_CODE_JOB_FAILED,
        ERROR_CODE_CANCELED,
        ERROR_CODE_STALE_JOB,
        ERROR_CODE_FAILED,
        ERROR_CODE_INTERNAL_ERROR,
    }
)


def normalize_error_code(raw_code: str | None) -> str:
    code = (raw_code or "").strip().upper()
    return code if code in ERROR_CODES else ERROR_CODE_INTERNAL_ERROR


@dataclass
class A2AError(Exception):
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.code = normalize_error_code(self.code)
        self.message = str(self.message)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
        }
        if self.details:
            payload["details"] = dict(self.details)
        return payload

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"
