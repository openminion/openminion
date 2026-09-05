from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from openminion.modules.brain.constants import BRAIN_ACTION_STATUS_SUCCESS
from openminion.modules.task.project.checkpoints import load_latest_project_checkpoint
from openminion.modules.task.project.effects import (
    ProjectEffectRecord,
    ProjectEffectReplayDecision,
    ProjectEffectStatus,
    evaluate_project_effect_replay,
    load_project_effect_receipt,
    load_project_effect_record,
    save_project_effect_record,
)
from openminion.modules.task.project.models import (
    ProjectCheckpoint,
    ProjectPermissionCheckResult,
)
from openminion.modules.task.project.policy import (
    consume_project_permission_grant,
    evaluate_project_permission,
)
from openminion.modules.task.project.turn import project_workspace
from openminion.modules.tool.contracts.model_ids import MODEL_GIT_PUSH, MODEL_GIT_TAG
from openminion.modules.tool.diagnostics.events import (
    emit_tool_invoke_operation_for_context,
)
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.tools.git.errors import GIT_REMOTE_OUTCOME_UNCERTAIN
from openminion.tools.git.runtime import (
    require_configured_git_remote,
    resolve_git_ref_oid,
    resolve_git_remote_ref_oid,
    resolve_git_repo_root,
)


@dataclass(frozen=True)
class GitRemoteProjectAction:
    tool_name: str
    repository: str
    remote: str
    source_ref: str
    ref: str
    expected_oid: str
    previous_oid: str | None
    expected_target_oid: str | None = None

    @property
    def scope(self) -> str:
        fields = (
            f"repository={self.repository};remote={self.remote};"
            f"source_ref={self.source_ref};ref={self.ref};"
            f"expected_oid={self.expected_oid}"
        )
        if self.expected_target_oid:
            fields += f";expected_target_oid={self.expected_target_oid}"
        return fields

    @property
    def precondition_refs(self) -> tuple[str, ...]:
        refs = (
            f"git:repository:{self.repository}",
            f"git:local:{self.source_ref}@{self.expected_oid}",
            f"git:remote:{self.remote}:{self.ref}@{self.previous_oid or 'absent'}",
        )
        if self.expected_target_oid:
            return (*refs, f"git:target:{self.ref}@{self.expected_target_oid}")
        return refs


@dataclass(frozen=True)
class GitRemoteProjectEffect:
    task_id: str
    project_run_id: str
    action: GitRemoteProjectAction
    effect: ProjectEffectRecord

    def facts(self) -> dict[str, Any]:
        return {
            "project_task_id": self.task_id,
            "project_run_id": self.project_run_id,
            "project_permission_grant_id": self.effect.approval_ref,
            "project_effect_id": self.effect.effect_id,
            "project_effect_status": self.effect.status.value,
            "repository_action_scope": self.action.scope,
            "repository_ref": self.action.ref,
            "repository_expected_oid": self.action.expected_oid,
        }


def git_push_action_scope(args: Mapping[str, Any], ctx: Any) -> str:
    return _git_push_action(args, ctx).scope


def git_tag_push_action_scope(args: Mapping[str, Any], ctx: Any) -> str:
    return _git_tag_push_action(args, ctx).scope


def _git_push_action(args: Mapping[str, Any], ctx: Any) -> GitRemoteProjectAction:
    repository = str(resolve_git_repo_root(ctx))
    remote = str(args.get("remote") or "").strip()
    source_ref = str(args.get("source_ref") or "").strip()
    target_ref = str(args.get("target_ref") or "").strip()
    require_configured_git_remote(repository, remote)
    return GitRemoteProjectAction(
        tool_name=MODEL_GIT_PUSH,
        repository=repository,
        remote=remote,
        source_ref=source_ref,
        ref=target_ref,
        expected_oid=resolve_git_ref_oid(repository, source_ref),
        previous_oid=resolve_git_remote_ref_oid(
            repository,
            remote=remote,
            ref=target_ref,
        ),
    )


def _git_tag_push_action(args: Mapping[str, Any], ctx: Any) -> GitRemoteProjectAction:
    repository = str(resolve_git_repo_root(ctx))
    remote = str(args.get("remote") or "").strip()
    tag_ref = f"refs/tags/{str(args.get('name') or '').strip()}"
    require_configured_git_remote(repository, remote)
    return GitRemoteProjectAction(
        tool_name=MODEL_GIT_TAG,
        repository=repository,
        remote=remote,
        source_ref=tag_ref,
        ref=tag_ref,
        expected_oid=resolve_git_ref_oid(repository, tag_ref),
        previous_oid=resolve_git_remote_ref_oid(
            repository,
            remote=remote,
            ref=tag_ref,
        ),
        expected_target_oid=resolve_git_ref_oid(repository, f"{tag_ref}^{{}}"),
    )


def _project_action(
    tool_name: str,
    args: Mapping[str, Any],
    ctx: Any,
) -> GitRemoteProjectAction:
    if tool_name == MODEL_GIT_PUSH:
        return _git_push_action(args, ctx)
    if tool_name == MODEL_GIT_TAG and args.get("action") == "push":
        return _git_tag_push_action(args, ctx)
    raise ValueError(f"unsupported project Git action: {tool_name}")


def _load_git_project_checkpoint(task_manager: Any, task_id: str) -> ProjectCheckpoint:
    checkpoint = load_latest_project_checkpoint(task_manager, task_id=task_id)
    if checkpoint is None:
        raise ToolRuntimeError(
            "NOT_FOUND",
            "Project tool execution requires a current project checkpoint.",
            {"reason_code": "project_checkpoint_missing", "project_task_id": task_id},
        )
    return checkpoint


def _require_project_repository(
    checkpoint: ProjectCheckpoint,
    repository: str,
) -> None:
    expected = project_workspace(checkpoint.project_run.workspace_ref)
    if Path(repository) != expected:
        raise ToolRuntimeError(
            "POLICY_DENIED",
            "Git action is outside the project execution repository.",
            {
                "reason_code": "project_repository_mismatch",
                "project_repository": str(expected),
                "requested_repository": repository,
            },
        )


def _raise_policy_denial(
    *,
    checkpoint: ProjectCheckpoint,
    permission: ProjectPermissionCheckResult,
    action: GitRemoteProjectAction,
    task_id: str,
    ctx: Any,
) -> None:
    facts = {
        "project_task_id": task_id,
        "project_run_id": checkpoint.project_run.project_run_id,
        "project_permission_decision": permission.decision.value,
        "project_permission_grant_id": permission.grant_id,
        "repository_action_scope": action.scope,
        "repository_ref": action.ref,
        "repository_expected_oid": action.expected_oid,
    }
    emit_tool_invoke_operation_for_context(
        ctx=ctx,
        operation="blocked_by_policy",
        tool_name=action.tool_name,
        status="error",
        error_code="POLICY_DENIED",
        extra=facts,
    )
    raise ToolRuntimeError(
        "POLICY_DENIED",
        permission.reason,
        {"reason_code": permission.decision.value, **facts},
    )


def begin_git_remote_project_effect(
    *,
    task_manager: Any,
    task_id: str,
    tool_name: str,
    idempotency_key: str,
    actor_ref: str,
    args: Mapping[str, Any],
    ctx: Any,
) -> GitRemoteProjectEffect:
    checkpoint = _load_git_project_checkpoint(task_manager, task_id)
    repository = str(resolve_git_repo_root(ctx))
    _require_project_repository(checkpoint, repository)
    action = _project_action(tool_name, args, ctx)
    action_key = idempotency_key.strip() or action.scope
    effect_id = f"effect:{action.tool_name}:{action_key}"
    existing = load_project_effect_record(
        task_manager,
        task_id=task_id,
        effect_id=effect_id,
    )
    if existing is not None:
        action = _action_with_recorded_remote_state(existing, action)
    replay = evaluate_project_effect_replay(
        existing,
        idempotency_key=action_key,
        precondition_refs=action.precondition_refs,
    )
    if replay.decision == ProjectEffectReplayDecision.BLOCK_STALE_PRECONDITION:
        raise ToolRuntimeError(
            "INVALID_REQUEST",
            "The repository state changed since this project action was recorded.",
            {
                "reason_code": replay.reason,
                "project_task_id": task_id,
                "project_effect_id": effect_id,
                "repository_action_scope": action.scope,
            },
        )
    if existing is not None and replay.decision in {
        ProjectEffectReplayDecision.REUSE_EXISTING,
        ProjectEffectReplayDecision.BLOCK_DUPLICATE,
    }:
        return _reuse_or_reconcile_git_effect(
            task_manager=task_manager,
            checkpoint=checkpoint,
            existing=existing,
            action=action,
            ctx=ctx,
        )

    permission = evaluate_project_permission(
        task_manager,
        task_id=task_id,
        tool_name=action.tool_name,
        scope=action.scope,
    )
    if not permission.allowed:
        _raise_policy_denial(
            checkpoint=checkpoint,
            permission=permission,
            action=action,
            task_id=task_id,
            ctx=ctx,
        )

    effect = ProjectEffectRecord(
        effect_id=effect_id,
        task_id=task_id,
        idempotency_key=action_key,
        actor_ref=actor_ref,
        capability_ref=action.tool_name,
        precondition_refs=action.precondition_refs,
        approval_ref=permission.grant_id,
    )
    save_project_effect_record(task_manager, effect)
    consume_project_permission_grant(
        task_manager,
        task_id=task_id,
        grant_id=str(permission.grant_id or ""),
    )
    started = GitRemoteProjectEffect(
        task_id=task_id,
        project_run_id=checkpoint.project_run.project_run_id,
        action=action,
        effect=effect,
    )
    ctx.git_remote_expected_before_oid = action.previous_oid
    emit_tool_invoke_operation_for_context(
        ctx=ctx,
        operation="invoke",
        tool_name=action.tool_name,
        extra=started.facts(),
    )
    return started


def _action_with_recorded_remote_state(
    existing: ProjectEffectRecord,
    action: GitRemoteProjectAction,
) -> GitRemoteProjectAction:
    local_ref = f"git:local:{action.source_ref}@{action.expected_oid}"
    repository_ref = f"git:repository:{action.repository}"
    remote_prefix = f"git:remote:{action.remote}:{action.ref}@"
    if repository_ref not in existing.precondition_refs:
        return action
    if local_ref not in existing.precondition_refs:
        return action
    if action.expected_target_oid and (
        f"git:target:{action.ref}@{action.expected_target_oid}"
        not in existing.precondition_refs
    ):
        return action
    recorded_remote = next(
        (
            ref.removeprefix(remote_prefix)
            for ref in existing.precondition_refs
            if ref.startswith(remote_prefix)
        ),
        None,
    )
    if recorded_remote is None:
        return action
    return replace(
        action,
        previous_oid=None if recorded_remote == "absent" else recorded_remote,
    )


def execute_git_remote_project_effect(
    *,
    task_manager: Any,
    task_id: str,
    tool_name: str,
    idempotency_key: str,
    actor_ref: str,
    args: Mapping[str, Any],
    ctx: Any,
    invoke: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    started = begin_git_remote_project_effect(
        task_manager=task_manager,
        task_id=task_id,
        tool_name=tool_name,
        idempotency_key=idempotency_key,
        actor_ref=actor_ref,
        args=args,
        ctx=ctx,
    )
    try:
        return _finalize_git_project_result(
            task_manager,
            started,
            result=invoke(),
            ctx=ctx,
        )
    except ToolRuntimeError as exc:
        facts = _fail_git_project_effect(
            task_manager,
            started,
            error=exc,
            ctx=ctx,
        )
        exc.details.update(facts)
        raise


def _finalize_git_project_result(
    task_manager: Any,
    started: GitRemoteProjectEffect,
    *,
    result: dict[str, Any],
    ctx: Any,
) -> dict[str, Any]:
    outputs = result.get("outputs")
    if result.get("status") == BRAIN_ACTION_STATUS_SUCCESS and isinstance(
        outputs, Mapping
    ):
        result["outputs"] = _complete_git_project_effect(
            task_manager,
            started,
            result=outputs,
            ctx=ctx,
        )
        return result

    raw_error = result.get("error")
    error = raw_error if isinstance(raw_error, Mapping) else {}
    raw_details = error.get("details")
    failure = ToolRuntimeError(
        str(error.get("code") or "GIT_BINARY_ERROR"),
        str(result.get("summary") or "Git remote action failed"),
        dict(raw_details) if isinstance(raw_details, Mapping) else {},
    )
    facts = _fail_git_project_effect(
        task_manager,
        started,
        error=failure,
        ctx=ctx,
    )
    if isinstance(raw_error, dict):
        raw_error["details"] = {
            **(dict(raw_details) if isinstance(raw_details, Mapping) else {}),
            **facts,
        }
    return result


def _complete_git_project_effect(
    task_manager: Any,
    started: GitRemoteProjectEffect,
    *,
    result: Mapping[str, Any],
    ctx: Any,
) -> dict[str, Any]:
    receipt = _git_receipt(started.action, result)
    terminal = started.effect.model_copy(
        update={
            "status": ProjectEffectStatus.SUCCEEDED,
            "result_ref": (
                f"git:remote:{started.action.remote}:{started.action.ref}"
                f"@{started.action.expected_oid}"
            ),
            "non_reversible_reason": (
                "OpenMinion does not automatically reverse published Git refs."
            ),
        }
    )
    save_project_effect_record(task_manager, terminal, receipt=receipt)
    completed = GitRemoteProjectEffect(
        task_id=started.task_id,
        project_run_id=started.project_run_id,
        action=started.action,
        effect=terminal,
    )
    facts = completed.facts()
    emit_tool_invoke_operation_for_context(
        ctx=ctx,
        operation="completed",
        tool_name=started.action.tool_name,
        extra=facts,
    )
    enriched = dict(result)
    raw_parsed = result.get("parsed")
    parsed = dict(raw_parsed) if isinstance(raw_parsed, Mapping) else {}
    enriched["parsed"] = {**parsed, **facts}
    return enriched


def _fail_git_project_effect(
    task_manager: Any,
    started: GitRemoteProjectEffect,
    *,
    error: BaseException,
    ctx: Any,
) -> dict[str, Any]:
    uncertain = isinstance(error, ToolRuntimeError) and (
        error.code == GIT_REMOTE_OUTCOME_UNCERTAIN
    )
    effect = started.effect
    if not uncertain:
        effect = effect.model_copy(update={"status": ProjectEffectStatus.FAILED})
        save_project_effect_record(task_manager, effect)
    current = GitRemoteProjectEffect(
        task_id=started.task_id,
        project_run_id=started.project_run_id,
        action=started.action,
        effect=effect,
    )
    facts = {**current.facts(), "project_effect_uncertain": uncertain}
    emit_tool_invoke_operation_for_context(
        ctx=ctx,
        operation="completed",
        tool_name=started.action.tool_name,
        status="error",
        error_code="PROJECT_EFFECT_UNCERTAIN" if uncertain else "PROJECT_EFFECT_FAILED",
        extra=facts,
    )
    return facts


def _reuse_or_reconcile_git_effect(
    *,
    task_manager: Any,
    checkpoint: ProjectCheckpoint,
    existing: ProjectEffectRecord,
    action: GitRemoteProjectAction,
    ctx: Any,
) -> GitRemoteProjectEffect:
    receipt = load_project_effect_receipt(
        task_manager,
        task_id=existing.task_id,
        effect_id=existing.effect_id,
    )
    if existing.status == ProjectEffectStatus.STARTED:
        observed_oid = resolve_git_remote_ref_oid(
            action.repository,
            remote=action.remote,
            ref=action.ref,
        )
        observed_target_oid = (
            resolve_git_remote_ref_oid(
                action.repository,
                remote=action.remote,
                ref=f"{action.ref}^{{}}",
            )
            if action.expected_target_oid
            else None
        )
        if observed_oid != action.expected_oid or (
            action.expected_target_oid
            and observed_target_oid != action.expected_target_oid
        ):
            raise ToolRuntimeError(
                GIT_REMOTE_OUTCOME_UNCERTAIN,
                "The prior Git remote update is still uncertain; it was not repeated.",
                {
                    "reason_code": "git_remote_readback_mismatch",
                    "project_task_id": existing.task_id,
                    "project_effect_id": existing.effect_id,
                    "project_effect_status": existing.status.value,
                    "repository_action_scope": action.scope,
                },
            )
        receipt = _readback_receipt(
            action,
            observed_oid=observed_oid,
            observed_target_oid=observed_target_oid,
        )
        existing = existing.model_copy(
            update={
                "status": ProjectEffectStatus.SUCCEEDED,
                "result_ref": (
                    f"git:remote:{action.remote}:{action.ref}@{action.expected_oid}"
                ),
                "verification_refs": (f"{action.tool_name}:readback",),
                "non_reversible_reason": (
                    "OpenMinion does not automatically reverse published Git refs."
                ),
            }
        )
        save_project_effect_record(task_manager, existing, receipt=receipt)
    if receipt is None:
        raise ToolRuntimeError(
            "INTERNAL_ERROR",
            "The completed project effect has no Git receipt.",
            {
                "reason_code": "project_effect_receipt_missing",
                "project_task_id": existing.task_id,
                "project_effect_id": existing.effect_id,
            },
        )
    current = GitRemoteProjectEffect(
        task_id=existing.task_id,
        project_run_id=checkpoint.project_run.project_run_id,
        action=action,
        effect=existing,
    )
    ctx.git_remote_expected_before_oid = action.previous_oid
    ctx.git_remote_reconciled_result = _git_result_from_receipt(receipt)
    return current


def _git_receipt(
    action: GitRemoteProjectAction,
    result: Mapping[str, Any],
) -> dict[str, Any]:
    raw_parsed = result.get("parsed")
    parsed = raw_parsed if isinstance(raw_parsed, Mapping) else {}
    return _readback_receipt(
        action,
        observed_oid=str(parsed.get("remote_oid") or ""),
        observed_target_oid=(
            str(parsed.get("remote_target_oid") or "")
            if action.expected_target_oid
            else None
        ),
    )


def _readback_receipt(
    action: GitRemoteProjectAction,
    *,
    observed_oid: str | None,
    observed_target_oid: str | None,
) -> dict[str, Any]:
    return {
        "tool_name": action.tool_name,
        "repository": action.repository,
        "remote": action.remote,
        "source_ref": action.source_ref,
        "ref": action.ref,
        "expected_oid": action.expected_oid,
        "previous_remote_oid": action.previous_oid,
        "remote_oid": observed_oid or "",
        "expected_target_oid": action.expected_target_oid,
        "remote_target_oid": observed_target_oid,
    }


def _git_result_from_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
    tool_name = str(receipt.get("tool_name") or "")
    ref = str(receipt.get("ref") or "")
    parsed = {
        "action": "push",
        "repository": str(receipt.get("repository") or ""),
        "remote": str(receipt.get("remote") or ""),
        "previous_remote_oid": receipt.get("previous_remote_oid"),
        "remote_oid": str(receipt.get("remote_oid") or ""),
        "reconciled": True,
    }
    if tool_name == MODEL_GIT_TAG:
        parsed.update(
            {
                "name": ref.removeprefix("refs/tags/"),
                "tag_ref": ref,
                "tag_oid": str(receipt.get("expected_oid") or ""),
                "target_oid": str(receipt.get("expected_target_oid") or ""),
                "remote_target_oid": receipt.get("remote_target_oid"),
            }
        )
    else:
        parsed.update(
            {
                "source_ref": str(receipt.get("source_ref") or ""),
                "source_oid": str(receipt.get("expected_oid") or ""),
                "target_ref": ref,
            }
        )
    return {
        "command": [],
        "exit_code": 0,
        "parsed": parsed,
        "raw_stdout": "",
        "raw_stderr": "",
    }


__all__ = [
    "GitRemoteProjectAction",
    "GitRemoteProjectEffect",
    "begin_git_remote_project_effect",
    "execute_git_remote_project_effect",
    "git_push_action_scope",
    "git_tag_push_action_scope",
]
