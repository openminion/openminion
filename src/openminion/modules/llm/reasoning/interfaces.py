from typing import Any, ClassVar, Protocol

from .schemas import (
    ModeThinkingPolicy,
    ThinkingRequest,
    ThinkingResolved,
    ThinkingResolutionInput,
)

THINKING_INTERFACE_VERSION = "v1"
_REQUIRED_METHODS = (
    "is_enabled",
    "get_version",
    "normalize_profile",
    "resolve",
    "resolve_mode_aware",
    "build_provider_metadata",
    "build_context_hints",
)


class ThinkingCtlInterface(Protocol):
    """Thinking module interface contract."""

    contract_version: ClassVar[str] = THINKING_INTERFACE_VERSION

    def __init__(self) -> None: ...

    def is_enabled(self) -> bool: ...

    def get_version(self) -> str: ...

    def normalize_profile(self, raw_value: Any) -> str | None: ...

    def resolve(
        self,
        *,
        request: ThinkingRequest,
        layers: ThinkingResolutionInput,
    ) -> ThinkingResolved: ...

    def resolve_mode_aware(
        self,
        *,
        request: ThinkingRequest,
        layers: ThinkingResolutionInput,
        mode_policy: ModeThinkingPolicy | None,
        mode_name: str | None = None,
    ) -> ThinkingResolved: ...

    def build_provider_metadata(
        self,
        *,
        resolved: ThinkingResolved,
    ) -> dict[str, str]: ...

    def build_context_hints(
        self,
        *,
        resolved: ThinkingResolved,
    ) -> dict[str, Any]: ...


def ensure_thinking_compatibility(
    ctl: Any, strict: bool = True
) -> tuple[bool, list[str]]:
    """Validate thinking controller implements the required interface."""
    errors: list[str] = []

    # Check contract version
    if not hasattr(ctl, "contract_version"):
        errors.append("Missing contract_version attribute")
    elif ctl.contract_version != THINKING_INTERFACE_VERSION:
        errors.append(
            f"Version mismatch: expected {THINKING_INTERFACE_VERSION}, "
            f"got {ctl.contract_version}"
        )

    # Check required methods
    for method in _REQUIRED_METHODS:
        if not hasattr(ctl, method) or not callable(getattr(ctl, method)):
            errors.append(f"Missing required method: {method}")

    if errors:
        if strict:

            class ThinkingError(Exception):
                def __init__(self, code, message):
                    self.code = code
                    self.message = message

            raise ThinkingError(
                "THINKING_INTERFACE_VIOLATION",
                f"Thinking controller incompatible: {errors}",
            )
        return False, errors

    return True, []
