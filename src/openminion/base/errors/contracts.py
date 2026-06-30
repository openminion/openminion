from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ErrorInfo:
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    namespace: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "code", str(self.code or "").strip() or "INTERNAL_ERROR"
        )
        message = str(self.message or "").strip()
        object.__setattr__(self, "message", message or "Internal error")
        details = self.details
        if isinstance(details, Mapping):
            normalized_details = dict(details)
        else:
            normalized_details = {"value": details} if details is not None else {}
        object.__setattr__(self, "details", normalized_details)
        namespace = str(self.namespace or "").strip() or None
        object.__setattr__(self, "namespace", namespace)

    def to_dict(
        self,
        *,
        include_details: bool = True,
        include_empty_details: bool = True,
        include_namespace: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
        }
        if include_details and (include_empty_details or self.details):
            payload["details"] = dict(self.details)
        if include_namespace and self.namespace:
            payload["namespace"] = self.namespace
        return payload
