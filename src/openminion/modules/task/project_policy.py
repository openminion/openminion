from __future__ import annotations

from openminion.modules.task.autonomy import now_ms
from openminion.modules.task.runtime.lifecycle import TaskManager

from .project_models import (
    ProjectBudgetPolicy,
    ProjectPermissionCheckResult,
    ProjectPermissionDecision,
    ProjectPermissionGrant,
    ProjectPolicyState,
)


_PROJECT_POLICY_METADATA_KEY = "project_policy"


def load_project_policy_state(
    task_manager: TaskManager,
    *,
    task_id: str,
) -> ProjectPolicyState | None:
    record = task_manager.get_task(task_id)
    if record is None:
        raise KeyError(f"task not found: {task_id}")
    raw = record.metadata.get(_PROJECT_POLICY_METADATA_KEY)
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("project policy metadata must be an object")
    return ProjectPolicyState.model_validate(raw)


def save_project_policy_state(
    task_manager: TaskManager,
    policy_state: ProjectPolicyState,
) -> ProjectPolicyState:
    record = task_manager.get_task(policy_state.task_id)
    if record is None:
        raise KeyError(f"task not found: {policy_state.task_id}")
    metadata = dict(record.metadata)
    refreshed = policy_state.model_copy(update={"updated_at_ms": now_ms()})
    metadata[_PROJECT_POLICY_METADATA_KEY] = refreshed.model_dump(mode="json")
    task_manager.update_task_metadata(
        task_id=policy_state.task_id,
        metadata=metadata,
    )
    return refreshed


def build_project_policy_state(
    task_manager: TaskManager,
    *,
    task_id: str,
    budget: ProjectBudgetPolicy | None = None,
    denied_tool_names: tuple[str, ...] = (),
) -> ProjectPolicyState:
    record = task_manager.get_task(task_id)
    if record is None:
        raise KeyError(f"task not found: {task_id}")
    project_run_id = str(record.metadata.get("project_run_id") or "").strip()
    if not project_run_id:
        raise ValueError("task is not linked to a project run")
    return ProjectPolicyState(
        task_id=task_id,
        project_run_id=project_run_id,
        denied_tool_names=tuple(
            sorted({name.strip() for name in denied_tool_names if name.strip()})
        ),
        budget=budget or ProjectBudgetPolicy(),
    )


def issue_project_permission_grant(
    task_manager: TaskManager,
    *,
    task_id: str,
    grant_id: str,
    tool_name: str,
    scope: str,
    expires_at_ms: int,
    destructive_allowed: bool = False,
    max_uses: int | None = None,
    reason: str | None = None,
    issued_at_ms: int | None = None,
) -> ProjectPolicyState:
    state = load_project_policy_state(task_manager, task_id=task_id)
    if state is None:
        state = build_project_policy_state(task_manager, task_id=task_id)
    issued = now_ms() if issued_at_ms is None else int(issued_at_ms)
    grant = ProjectPermissionGrant(
        grant_id=grant_id,
        tool_name=_normalize_project_tool_name(tool_name),
        scope=str(scope or "").strip(),
        issued_at_ms=issued,
        expires_at_ms=expires_at_ms,
        destructive_allowed=destructive_allowed,
        max_uses=max_uses,
        reason=str(reason or "").strip() or None,
    )
    grants = tuple(
        existing for existing in state.grants if existing.grant_id != grant.grant_id
    ) + (grant,)
    return save_project_policy_state(
        task_manager,
        state.model_copy(update={"grants": grants}),
    )


def evaluate_project_permission(
    task_manager: TaskManager,
    *,
    task_id: str,
    tool_name: str,
    scope: str,
    destructive: bool = False,
    at_ms: int | None = None,
) -> ProjectPermissionCheckResult:
    normalized_tool = _normalize_project_tool_name(tool_name)
    normalized_scope = str(scope or "").strip()
    if not normalized_scope:
        raise ValueError("scope is required")
    state = load_project_policy_state(task_manager, task_id=task_id)
    if state is None:
        return ProjectPermissionCheckResult(
            decision=ProjectPermissionDecision.APPROVAL_REQUIRED,
            tool_name=normalized_tool,
            scope=normalized_scope,
            reason="project policy state is not configured",
        )
    denied = {_normalize_project_tool_name(name) for name in state.denied_tool_names}
    if normalized_tool in denied:
        return ProjectPermissionCheckResult(
            decision=ProjectPermissionDecision.DENIED,
            tool_name=normalized_tool,
            scope=normalized_scope,
            reason="tool is denied by project policy",
        )

    now = now_ms() if at_ms is None else int(at_ms)
    for grant in state.grants:
        if grant.tool_name != normalized_tool or grant.scope != normalized_scope:
            continue
        if now >= grant.expires_at_ms:
            return ProjectPermissionCheckResult(
                decision=ProjectPermissionDecision.EXPIRED,
                tool_name=normalized_tool,
                scope=normalized_scope,
                grant_id=grant.grant_id,
                reason="grant expired",
                expires_at_ms=grant.expires_at_ms,
            )
        if grant.max_uses is not None and grant.uses >= grant.max_uses:
            return ProjectPermissionCheckResult(
                decision=ProjectPermissionDecision.EXPIRED,
                tool_name=normalized_tool,
                scope=normalized_scope,
                grant_id=grant.grant_id,
                reason="grant use limit exhausted",
                expires_at_ms=grant.expires_at_ms,
            )
        if destructive and (
            state.budget.destructive_requires_confirmation
            and not grant.destructive_allowed
        ):
            return ProjectPermissionCheckResult(
                decision=ProjectPermissionDecision.DENIED,
                tool_name=normalized_tool,
                scope=normalized_scope,
                grant_id=grant.grant_id,
                reason="destructive action requires an explicit destructive grant",
                expires_at_ms=grant.expires_at_ms,
            )
        return ProjectPermissionCheckResult(
            decision=ProjectPermissionDecision.ALLOWED,
            tool_name=normalized_tool,
            scope=normalized_scope,
            grant_id=grant.grant_id,
            reason="matched project permission grant",
            expires_at_ms=grant.expires_at_ms,
        )

    return ProjectPermissionCheckResult(
        decision=ProjectPermissionDecision.APPROVAL_REQUIRED,
        tool_name=normalized_tool,
        scope=normalized_scope,
        reason="no matching project permission grant",
    )


def consume_project_permission_grant(
    task_manager: TaskManager,
    *,
    task_id: str,
    grant_id: str,
) -> ProjectPolicyState:
    state = load_project_policy_state(task_manager, task_id=task_id)
    if state is None:
        raise KeyError(f"project policy state not found: {task_id}")
    grants: list[ProjectPermissionGrant] = []
    found = False
    for grant in state.grants:
        if grant.grant_id != grant_id:
            grants.append(grant)
            continue
        found = True
        if grant.max_uses is not None and grant.uses >= grant.max_uses:
            raise ValueError("project permission grant use limit exhausted")
        grants.append(grant.model_copy(update={"uses": grant.uses + 1}))
    if not found:
        raise KeyError(f"project permission grant not found: {grant_id}")
    return save_project_policy_state(
        task_manager,
        state.model_copy(update={"grants": tuple(grants)}),
    )


def _normalize_project_tool_name(tool_name: str) -> str:
    normalized = str(tool_name or "").strip().lower()
    if not normalized:
        raise ValueError("tool_name is required")
    return normalized



__all__ = [
    "build_project_policy_state",
    "consume_project_permission_grant",
    "evaluate_project_permission",
    "issue_project_permission_grant",
    "load_project_policy_state",
    "save_project_policy_state",
]
