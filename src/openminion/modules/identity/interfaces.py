from typing import Any, ClassVar, Protocol, runtime_checkable


IDENTITY_INTERFACE_VERSION = "v1"
IDENTITY_REPOSITORY_INTERFACE_VERSION = "v1"
IDENTITY_DEFAULT_RENDER_VERSION = "v1"


def _compatibility_error(code: str, message: str) -> RuntimeError:
    error = RuntimeError(message)
    setattr(error, "code", code)
    setattr(error, "message", message)
    return error


class IdentityCtlInterface(Protocol):
    """Identity Control Interface Contract."""

    contract_version: ClassVar[str] = IDENTITY_INTERFACE_VERSION

    def __init__(
        self,
        *,
        store: Any,  # IdentityStore,
        skillctl: Any | None = None,
        render_version: str = IDENTITY_DEFAULT_RENDER_VERSION,
        bullet_prefix: str = "- ",
        section_headers: bool = False,
    ) -> None: ...

    @property
    def resolved_render_version(self) -> str: ...

    def close(self) -> None: ...

    def get_profile(self, agent_id: str) -> Any | None: ...  # AgentProfile | None

    def list_profiles(self) -> list[Any]: ...  # AgentProfileSummary[]

    def upsert_profile(
        self,
        profile: Any,  # AgentProfile
        actor: str | None = None,
        reason: str | None = None,
    ) -> str: ...

    def delete_profile(self, agent_id: str) -> None: ...

    def render(
        self,
        agent_id: str,
        purpose: str,
        max_tokens: int,
        max_chars: int | None = None,
        provider_pref: str | None = None,
        query_text: str | None = None,
    ) -> Any: ...  # IdentitySnippet

    def render_from_profile(
        self,
        profile: Any,  # AgentProfile
        purpose: str,
        max_tokens: int,
        max_chars: int | None = None,
        provider_pref: str | None = None,
        query_text: str | None = None,
    ) -> Any: ...  # IdentitySnippet

    def validate_profile(
        self, profile: Any | dict[str, Any]
    ) -> Any: ...  # ValidationResult

    def validate_render(
        self, snippet: Any | dict[str, Any]
    ) -> Any: ...  # ValidationResult

    def warm_cache(
        self, agent_id: str, purposes: list[str] | None = None, max_tokens: int = 220
    ) -> int: ...

    def clear_cache(self, agent_id: str | None = None) -> None: ...

    def load_profiles_from_path(self, path: str) -> list[str]: ...


@runtime_checkable
class IdentityRepository(Protocol):
    """Module-owned identity repository protocol for tool runtime injection."""

    repository_contract_version: ClassVar[str]

    def get_profile(self, agent_id: str) -> Any | None: ...

    def upsert_profile(
        self,
        profile: Any,
        actor: str | None = None,
        reason: str | None = None,
    ) -> str: ...


def ensure_identity_compatibility(
    ctl: Any, strict: bool = True
) -> tuple[bool, list[str]]:
    """Validate identity controller implements the required interface."""
    errors: list[str] = []

    # Check contract version
    if not hasattr(ctl, "contract_version"):
        errors.append("Missing contract_version attribute")
    elif ctl.contract_version != IDENTITY_INTERFACE_VERSION:
        errors.append(
            f"Version mismatch: expected {IDENTITY_INTERFACE_VERSION}, "
            f"got {ctl.contract_version}"
        )

    # Check required methods
    required_methods = [
        "close",
        "get_profile",
        "list_profiles",
        "upsert_profile",
        "delete_profile",
        "render",
        "render_from_profile",
        "validate_profile",
        "validate_render",
        "warm_cache",
        "clear_cache",
        "load_profiles_from_path",
    ]

    for method in required_methods:
        if not hasattr(ctl, method) or not callable(getattr(ctl, method)):
            errors.append(f"Missing required method: {method}")

    if errors:
        if strict:
            raise _compatibility_error(
                "IDENTITY_CTL_INTERFACE_VIOLATION",
                f"Identity controller incompatible: {errors}",
            )
        return False, errors

    return True, []


def ensure_identity_repository_compatibility(
    repository: Any, strict: bool = True
) -> tuple[bool, list[str]]:
    """Validate identity repository contract used by tool runtime injection."""
    errors: list[str] = []
    version = str(
        getattr(
            repository,
            "repository_contract_version",
            getattr(repository, "contract_version", ""),
        )
        or ""
    ).strip()
    if version != IDENTITY_REPOSITORY_INTERFACE_VERSION:
        errors.append(
            "Version mismatch: expected "
            f"{IDENTITY_REPOSITORY_INTERFACE_VERSION}, got {version or '<missing>'}"
        )

    for method in ("get_profile", "upsert_profile"):
        if not hasattr(repository, method) or not callable(getattr(repository, method)):
            errors.append(f"Missing required method: {method}")

    if errors:
        if strict:
            raise _compatibility_error(
                "IDENTITY_REPOSITORY_INTERFACE_VIOLATION",
                f"Identity repository incompatible: {errors}",
            )
        return False, errors
    return True, []
