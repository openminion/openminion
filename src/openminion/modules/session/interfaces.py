from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from openminion.modules.task.scheduling.interfaces import (
    CRON_INTERFACE_VERSION,
    validate_cron_store_protocol,
)

SESSION_INTERFACE_VERSION = "v1"
SESSION_REPOSITORY_INTERFACE_VERSION = CRON_INTERFACE_VERSION
SESSION_CONTINUATION_SCHEMA_VERSION = "session_continuation.v1"
SESSION_TURN_LEASE_SCHEMA_VERSION = "session_turn_lease.v1"
SESSION_SHARE_SCHEMA_VERSION = "session_share.v1"
SESSION_SHARE_PROJECTION_VERSION = "session_share_projection.v1"
SESSION_RETENTION_PLAN_VERSION = "session_retention_plan.v1"
SESSION_RETENTION_HOLD_VERSION = "session_retention_hold.v1"
SESSION_ENCRYPTION_SCHEMA_VERSION = "session_encryption.v1"
SESSION_ENCRYPTION_MIGRATION_VERSION = "session_encryption_migration.v1"
SESSION_BRANCH_DIFF_SCHEMA_VERSION = "session_branch_diff.v1"
SESSION_BRANCH_CARRY_FORWARD_SCHEMA_VERSION = "session_branch_carry_forward.v1"
SESSION_TURN_BUSY_CODE = "SESSION_TURN_BUSY"

if TYPE_CHECKING:
    from .schemas import (
        ContinuationApplyResult,
        ContinuationBuildResult,
        ContinuationPreview,
        SessionContinuationPacket,
    )


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

    def acquire_session_turn_lease(
        self,
        session_id: str,
        *,
        owner: str,
        request_id: str,
        ttl_s: int = 60,
        now_iso: str | None = None,
    ) -> Any: ...

    def renew_session_turn_lease(
        self,
        session_id: str,
        *,
        owner: str,
        fence_token: int,
        ttl_s: int = 60,
        now_iso: str | None = None,
    ) -> bool: ...

    def release_session_turn_lease(
        self,
        session_id: str,
        *,
        owner: str,
        fence_token: int,
        now_iso: str | None = None,
    ) -> bool: ...

    def assert_session_turn_fence(
        self,
        session_id: str,
        *,
        fence_token: int,
    ) -> None: ...


@runtime_checkable
class SessionContextClientAPI(Protocol):
    contract_version: str

    def get_slice(
        self, *, session_id: str, purpose: str, limits: dict[str, int]
    ) -> Any: ...


@runtime_checkable
class SessionContinuationAPI(Protocol):
    """Explicit cross-session continuation without expanding SessionStoreAPI."""

    def preview(
        self,
        source_session_id: str,
        *,
        target_agent_id: str,
        expires_in_seconds: int = 86_400,
    ) -> "ContinuationPreview": ...

    def create(
        self,
        source_session_id: str,
        *,
        target_agent_id: str,
        expires_in_seconds: int = 86_400,
    ) -> "ContinuationBuildResult": ...

    def get_packet(self, packet_id: str) -> "SessionContinuationPacket": ...

    def apply(
        self,
        target_session_id: str,
        *,
        packet_id: str,
    ) -> "ContinuationApplyResult": ...


_REQUIRED_MEMBERS: dict[str, tuple[str, ...]] = {
    "store": (
        "contract_version",
        "append_turn",
        "append_event",
        "put_working_state",
        "get_latest_working_state",
        "get_slice",
        "acquire_session_turn_lease",
        "renew_session_turn_lease",
        "release_session_turn_lease",
        "assert_session_turn_fence",
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
