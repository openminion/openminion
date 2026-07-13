from typing import Any, Protocol, runtime_checkable

from openminion.modules.task.scheduling.interfaces import (
    CRON_INTERFACE_VERSION,
    validate_cron_store_protocol,
)

SESSION_INTERFACE_VERSION = "v1"
SESSION_REPOSITORY_INTERFACE_VERSION = CRON_INTERFACE_VERSION


@runtime_checkable
class SessionStoreAPI(Protocol):
    contract_version: str

    def append_turn(
        self, session_id: str, role: str, content: str, **kwargs: Any
    ) -> str: ...

    def append_event(
        self, session_id: str, type: str, payload: dict[str, Any], **kwargs: Any
    ) -> str: ...

    def put_working_state(
        self,
        session_id: str,
        *,
        state_ref: str | None = None,
        state_inline: dict[str, Any] | None = None,
    ) -> int: ...

    def get_latest_working_state(self, session_id: str) -> dict[str, Any] | None: ...

    def get_slice(
        self, session_id: str, purpose: str, limits: Any = None
    ) -> dict[str, Any]: ...


@runtime_checkable
class SessionContextClientAPI(Protocol):
    contract_version: str

    def get_slice(
        self, *, session_id: str, purpose: str, limits: dict[str, int]
    ) -> Any: ...


_REQUIRED_MEMBERS: dict[str, tuple[str, ...]] = {
    "store": (
        "contract_version",
        "append_turn",
        "append_event",
        "put_working_state",
        "get_latest_working_state",
        "get_slice",
    ),
    "context_client": (
        "contract_version",
        "get_slice",
    ),
}


def ensure_session_component_compatibility(
    component: Any, *, component_type: str
) -> None:
    normalized = str(component_type or "").strip().lower()
    required = _REQUIRED_MEMBERS.get(normalized)
    if required is None:
        raise ValueError(f"unknown component_type: {component_type}")

    missing: list[str] = []
    for name in required:
        if not hasattr(component, name):
            missing.append(name)
            continue
        value = getattr(component, name)
        if name == "contract_version":
            continue
        if not callable(value):
            missing.append(name)
    if missing:
        raise TypeError(
            f"{component.__class__.__name__} is incompatible with session {normalized} contract; missing members: {', '.join(missing)}"
        )

    version = str(getattr(component, "contract_version", "")).strip()
    if version != SESSION_INTERFACE_VERSION:
        raise TypeError(
            f"{component.__class__.__name__} has unsupported contract_version={version!r}; expected {SESSION_INTERFACE_VERSION!r}"
        )


def ensure_cron_repository_compatibility(repository: Any) -> None:
    """Validate cron repository contract used by tool runtime injection."""
    errors = validate_cron_store_protocol(repository)
    if errors:
        missing_members = [
            error.removeprefix("Missing required store method: ").strip()
            for error in errors
            if error.startswith("Missing required store method: ")
        ]
        version_errors = [
            error
            for error in errors
            if error
            not in set(
                f"Missing required store method: {name}" for name in missing_members
            )
        ]
        if missing_members:
            detail = f"missing members: {', '.join(missing_members)}"
            if version_errors:
                detail = f"{detail}; {'; '.join(version_errors)}"
        else:
            detail = "; ".join(errors)
        raise TypeError(
            f"{repository.__class__.__name__} is incompatible with cron repository contract; {detail}"
        )
