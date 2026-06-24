from typing import Any, Protocol, runtime_checkable

from .schemas import (
    PendingAction,
    PlanDraft,
    PlanRecord,
    ResumePointer,
    StepUpdateInput,
    TaskCreateInput,
    TaskDigest,
    TaskOps,
    TaskRecord,
    TaskStatus,
)


TASK_INTERFACE_VERSION = "v1"


@runtime_checkable
class TaskCtlInterface(Protocol):
    """Canonical task controller contract for brain/runtime/context integration."""

    contract_version: str

    def create_task(
        self, input: TaskCreateInput, *, trace_id: str | None = None
    ) -> TaskRecord: ...

    def attach_plan(
        self, task_id: str, draft: PlanDraft, *, trace_id: str | None = None
    ) -> PlanRecord: ...

    def step_update(
        self,
        task_id: str,
        step_id: str,
        input: StepUpdateInput,
        *,
        trace_id: str | None = None,
    ) -> PlanRecord: ...

    def transition_task(
        self,
        task_id: str,
        status: TaskStatus,
        *,
        trace_id: str | None = None,
    ) -> TaskRecord: ...

    def apply_ops(
        self, task_ops: TaskOps, *, trace_id: str | None = None
    ) -> list[str]: ...

    def get_task(self, task_id: str) -> TaskRecord: ...

    def get_digest(
        self, *, agent_id: str, session_id: str, limit: int = 5
    ) -> TaskDigest: ...

    def record_pending_action(
        self,
        *,
        policy_request_id: str,
        cursor: ResumePointer,
        reason: str | None = None,
    ) -> PendingAction: ...

    def resume_pending_action(
        self,
        *,
        policy_request_id: str,
        decision_id: str,
        trace_id: str | None = None,
    ) -> ResumePointer: ...

    def list_events(self) -> list[dict[str, Any]]: ...


def ensure_task_compatibility(
    ctl: Any,
    *,
    strict: bool = True,
) -> tuple[bool, list[str]]:
    """Validate task controller implementation compatibility."""

    required = (
        "contract_version",
        "create_task",
        "attach_plan",
        "step_update",
        "transition_task",
        "apply_ops",
        "get_task",
        "get_digest",
        "record_pending_action",
        "resume_pending_action",
        "list_events",
    )

    errors: list[str] = []
    for member in required:
        if not hasattr(ctl, member):
            errors.append(f"Missing required member: {member}")
            continue
        if member != "contract_version" and not callable(getattr(ctl, member)):
            errors.append(f"Member is not callable: {member}")

    version = str(getattr(ctl, "contract_version", "")).strip()
    if version != TASK_INTERFACE_VERSION:
        errors.append(
            f"Version mismatch: expected {TASK_INTERFACE_VERSION}, got {version or '<missing>'}"
        )

    if errors and strict:
        raise TypeError(
            f"{ctl.__class__.__name__} incompatible with task contract: {', '.join(errors)}"
        )

    return not errors, errors
