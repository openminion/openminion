from collections.abc import Mapping
from typing import Any, ClassVar, Protocol, runtime_checkable
from collections.abc import Callable


CRON_INTERFACE_VERSION = "v1"


class CronError(Exception):
    """Cron compatibility validation error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class CronSchedulerInterface(Protocol):
    """Cron Scheduler interface contract."""

    contract_version: ClassVar[str] = CRON_INTERFACE_VERSION

    def __init__(
        self,
        *,
        store: Any,  # CronStore,
        daemon_id: str | None = None,
        tick_seconds: float = 2.0,
        lease_ttl_seconds: int = 60,
        max_concurrent_runs: int = 4,
        execute_system_event: Callable[..., Any] | None = None,
        execute_agent_turn: Callable[..., Any] | None = None,
        delivery_handler: Callable[..., Any] | None = None,
        on_event: Callable[..., Any] | None = None,
    ) -> None: ...

    @property
    def daemon_id(self) -> str: ...

    def start(self) -> None: ...

    def shutdown(self, *, grace_s: float = 5.0) -> None: ...

    def status(self) -> dict[str, Any]: ...


@runtime_checkable
class CronStoreProtocol(Protocol):
    """Canonical cron store protocol used by scheduler, runtime, and task tooling."""

    contract_version: ClassVar[str] = CRON_INTERFACE_VERSION
    repository_contract_version: ClassVar[str] = CRON_INTERFACE_VERSION

    def add_cron_job(
        self,
        *,
        name: str,
        schedule: Mapping[str, Any],
        payload: Mapping[str, Any],
        description: str | None = None,
        enabled: bool = True,
        agent_id: str | None = None,
        session_target: str | None = None,
        wake_mode: str | None = None,
        delivery: Mapping[str, Any] | None = None,
        delete_after_run: bool | None = None,
        misfire_policy: str | Mapping[str, Any] | None = None,
        max_lateness_s: int = 600,
        max_concurrency: int = 1,
        job_id: str | None = None,
    ) -> str: ...

    def enqueue_due_cron_runs(
        self,
        daemon_id: str,
        *,
        lease_ttl_s: int = 60,
        max_jobs: int = 50,
        now_iso: str | None = None,
    ) -> list[dict[str, Any]]: ...

    def acquire_cron_runs(
        self,
        daemon_id: str,
        *,
        lease_ttl_s: int = 60,
        limit: int = 10,
        now_iso: str | None = None,
    ) -> list[dict[str, Any]]: ...

    def renew_cron_run_lease(
        self,
        run_id: str,
        *,
        daemon_id: str,
        lease_ttl_s: int = 60,
        now_iso: str | None = None,
    ) -> bool: ...

    def get_cron_job(self, job_id: str) -> dict[str, Any] | None: ...

    def list_cron_jobs(
        self,
        *,
        enabled: bool | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]: ...

    def delete_cron_job(self, job_id: str) -> None: ...

    def finish_cron_run(
        self,
        run_id: str,
        *,
        state: str,
        summary: str | None = None,
        artifact_refs: list[dict[str, Any]] | None = None,
        error: dict[str, Any] | None = None,
        isolated_session_id: str | None = None,
        now_iso: str | None = None,
    ) -> dict[str, Any] | None: ...

    def list_cron_runs(
        self,
        *,
        job_id: str | None = None,
        limit: int = 100,
        states: list[str] | None = None,
    ) -> list[dict[str, Any]]: ...


CronStoreInterface = CronStoreProtocol

_REQUIRED_CRON_STORE_METHODS = (
    "add_cron_job",
    "get_cron_job",
    "list_cron_jobs",
    "delete_cron_job",
    "list_cron_runs",
    "enqueue_due_cron_runs",
    "acquire_cron_runs",
    "renew_cron_run_lease",
    "finish_cron_run",
)


def _cron_store_version(value: Any) -> str:
    repository_version = getattr(value, "repository_contract_version", None)
    if isinstance(repository_version, str) and repository_version.strip():
        return repository_version.strip()
    contract_version = getattr(value, "contract_version", "")
    return str(contract_version or "").strip()


def validate_cron_store_protocol(store: Any) -> list[str]:
    """Return protocol validation errors without raising."""
    errors: list[str] = []
    version = _cron_store_version(store)
    if version != CRON_INTERFACE_VERSION:
        errors.append(
            f"Version mismatch: expected {CRON_INTERFACE_VERSION}, got {version or '<missing>'}"
        )

    for method in _REQUIRED_CRON_STORE_METHODS:
        if not hasattr(store, method) or not callable(getattr(store, method)):
            errors.append(f"Missing required store method: {method}")
    return errors


def ensure_cron_compatibility(
    scheduler: Any, strict: bool = True
) -> tuple[bool, list[str]]:
    """Validate cron scheduler implements the required interface."""
    errors = []

    if not hasattr(scheduler, "contract_version"):
        errors.append("Missing contract_version attribute")
    elif scheduler.contract_version != CRON_INTERFACE_VERSION:
        errors.append(
            f"Version mismatch: expected {CRON_INTERFACE_VERSION}, "
            f"got {scheduler.contract_version}"
        )

    required_scheduler_methods = ["start", "shutdown", "status"]

    for method in required_scheduler_methods:
        if not hasattr(scheduler, method) or not callable(getattr(scheduler, method)):
            errors.append(f"Missing required scheduler method: {method}")

    if errors:
        if strict:
            raise CronError(
                "CRON_SCHEDULER_INTERFACE_VIOLATION",
                f"Cron scheduler incompatible: {errors}",
            )
        return False, errors

    return True, []


def ensure_cron_store_compatibility(
    store: Any, strict: bool = True
) -> tuple[bool, list[str]]:
    """Validate cron store implements the required interface."""
    errors = validate_cron_store_protocol(store)

    if errors:
        if strict:
            raise CronError(
                "CRON_STORE_INTERFACE_VIOLATION", f"Cron store incompatible: {errors}"
            )
        return False, errors

    return True, []
