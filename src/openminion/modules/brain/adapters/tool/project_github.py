from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from openminion.modules.brain.constants import BRAIN_ACTION_STATUS_SUCCESS
from openminion.modules.task.project.checkpoints import (
    load_latest_project_checkpoint,
)
from openminion.modules.task.project.effects import (
    ProjectEffectRecord,
    ProjectEffectReplayDecision,
    ProjectEffectStatus,
    evaluate_project_effect_replay,
    load_project_effect_receipt,
    load_project_effect_record,
    save_project_effect_record,
)
from openminion.modules.task.project.policy import (
    consume_project_permission_grant,
    evaluate_project_permission,
)
from openminion.modules.tool.diagnostics.events import (
    emit_tool_invoke_operation_for_context,
)
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.tools.github.interfaces import TOOL_GITHUB_OPEN_PR
from openminion.tools.github.plugin import find_open_pr, resolve_open_pr_head_sha


@dataclass(frozen=True)
class GithubOpenPrProjectEffect:
    task_id: str
    project_run_id: str
    scope: str
    effect: ProjectEffectRecord
    head_sha: str

    def facts(self) -> dict[str, Any]:
        return {
            "project_task_id": self.task_id,
            "project_run_id": self.project_run_id,
            "project_permission_grant_id": self.effect.approval_ref,
            "project_effect_id": self.effect.effect_id,
            "project_effect_status": self.effect.status.value,
            "repository_action_scope": self.scope,
            "repository_head_sha": self.head_sha,
        }


def github_open_pr_action_scope(
    *,
    owner: str,
    repo: str,
    head: str,
    base: str,
    head_sha: str,
) -> str:
    return (
        f"repository={owner}/{repo};head={head};base={base};head_sha={head_sha}"
    )


def begin_github_open_pr_project_effect(
    *,
    task_manager: Any,
    task_id: str,
    idempotency_key: str,
    actor_ref: str,
    args: Mapping[str, Any],
    ctx: Any,
) -> GithubOpenPrProjectEffect:
    checkpoint = load_latest_project_checkpoint(task_manager, task_id=task_id)
    if checkpoint is None:
        raise ToolRuntimeError(
            "PROJECT_CHECKPOINT_MISSING",
            "Project tool execution requires a current project checkpoint.",
            {"reason_code": "project_checkpoint_missing", "project_task_id": task_id},
        )

    owner = str(args.get("owner") or "")
    repo = str(args.get("repo") or "")
    head = str(args.get("head") or "")
    base = str(args.get("base") or "")
    head_sha = resolve_open_pr_head_sha(args, ctx)
    scope = github_open_pr_action_scope(
        owner=owner,
        repo=repo,
        head=head,
        base=base,
        head_sha=head_sha,
    )
    action_key = idempotency_key.strip() or scope
    effect_id = f"effect:github.open_pr:{action_key}"
    precondition_refs = (
        f"github:repository:{owner}/{repo}",
        f"github:head:{head}@{head_sha}",
        f"github:base:{base}",
    )
    existing = load_project_effect_record(
        task_manager,
        task_id=task_id,
        effect_id=effect_id,
    )
    replay = evaluate_project_effect_replay(
        existing,
        idempotency_key=action_key,
        precondition_refs=precondition_refs,
    )
    if replay.decision == ProjectEffectReplayDecision.BLOCK_STALE_PRECONDITION:
        raise ToolRuntimeError(
            "PROJECT_EFFECT_STALE",
            "The repository state changed since this project action was recorded.",
            {
                "reason_code": replay.reason,
                "project_task_id": task_id,
                "project_effect_id": effect_id,
                "repository_action_scope": scope,
            },
        )

    if existing is not None and replay.decision in {
        ProjectEffectReplayDecision.REUSE_EXISTING,
        ProjectEffectReplayDecision.BLOCK_DUPLICATE,
    }:
        return _reuse_or_reconcile_open_pr_effect(
            task_manager=task_manager,
            checkpoint=checkpoint,
            existing=existing,
            scope=scope,
            head_sha=head_sha,
            args=args,
            ctx=ctx,
        )

    permission = evaluate_project_permission(
        task_manager,
        task_id=task_id,
        tool_name=TOOL_GITHUB_OPEN_PR,
        scope=scope,
    )
    if not permission.allowed:
        facts = {
            "project_task_id": task_id,
            "project_run_id": checkpoint.project_run.project_run_id,
            "project_permission_decision": permission.decision.value,
            "project_permission_grant_id": permission.grant_id,
            "repository_action_scope": scope,
            "repository_head_sha": head_sha,
        }
        emit_tool_invoke_operation_for_context(
            ctx=ctx,
            operation="blocked_by_policy",
            tool_name=TOOL_GITHUB_OPEN_PR,
            status="error",
            error_code="PROJECT_POLICY_DENIED",
            extra=facts,
        )
        raise ToolRuntimeError(
            "PROJECT_POLICY_DENIED",
            permission.reason,
            {"reason_code": permission.decision.value, **facts},
        )

    effect = ProjectEffectRecord(
        effect_id=effect_id,
        task_id=task_id,
        idempotency_key=action_key,
        actor_ref=actor_ref,
        capability_ref=TOOL_GITHUB_OPEN_PR,
        precondition_refs=precondition_refs,
        approval_ref=permission.grant_id,
    )
    save_project_effect_record(task_manager, effect)
    consume_project_permission_grant(
        task_manager,
        task_id=task_id,
        grant_id=str(permission.grant_id or ""),
    )
    started = GithubOpenPrProjectEffect(
        task_id=task_id,
        project_run_id=checkpoint.project_run.project_run_id,
        scope=scope,
        effect=effect,
        head_sha=head_sha,
    )
    ctx.github_open_pr_head_sha = head_sha
    emit_tool_invoke_operation_for_context(
        ctx=ctx,
        operation="invoke",
        tool_name=TOOL_GITHUB_OPEN_PR,
        extra=started.facts(),
    )
    return started


def complete_github_open_pr_project_effect(
    task_manager: Any,
    started: GithubOpenPrProjectEffect,
    *,
    result: Mapping[str, Any],
    ctx: Any,
) -> dict[str, Any]:
    receipt = _open_pr_receipt(result, head_sha=started.head_sha)
    terminal = started.effect.model_copy(
        update={
            "status": ProjectEffectStatus.SUCCEEDED,
            "result_ref": (
                f"github:pull:{receipt['owner']}/{receipt['repo']}"
                f"#{receipt['number']}"
            ),
            "non_reversible_reason": (
                "OpenMinion does not automatically close a created pull request."
            ),
        }
    )
    save_project_effect_record(task_manager, terminal, receipt=receipt)
    completed = GithubOpenPrProjectEffect(
        task_id=started.task_id,
        project_run_id=started.project_run_id,
        scope=started.scope,
        effect=terminal,
        head_sha=started.head_sha,
    )
    facts = completed.facts()
    emit_tool_invoke_operation_for_context(
        ctx=ctx,
        operation="completed",
        tool_name=TOOL_GITHUB_OPEN_PR,
        extra=facts,
    )
    return _with_project_facts(result, facts)


def fail_github_open_pr_project_effect(
    task_manager: Any,
    started: GithubOpenPrProjectEffect,
    *,
    error: BaseException,
    ctx: Any,
) -> dict[str, Any]:
    uncertain = _is_uncertain_github_error(error)
    effect = started.effect
    if not uncertain:
        effect = effect.model_copy(update={"status": ProjectEffectStatus.FAILED})
        save_project_effect_record(task_manager, effect)
    current = GithubOpenPrProjectEffect(
        task_id=started.task_id,
        project_run_id=started.project_run_id,
        scope=started.scope,
        effect=effect,
        head_sha=started.head_sha,
    )
    facts = {**current.facts(), "project_effect_uncertain": uncertain}
    emit_tool_invoke_operation_for_context(
        ctx=ctx,
        operation="completed",
        tool_name=TOOL_GITHUB_OPEN_PR,
        status="error",
        error_code="PROJECT_EFFECT_UNCERTAIN" if uncertain else "PROJECT_EFFECT_FAILED",
        extra=facts,
    )
    return facts


def finalize_github_open_pr_project_result(
    task_manager: Any,
    started: GithubOpenPrProjectEffect,
    *,
    result: dict[str, Any],
    ctx: Any,
) -> dict[str, Any]:
    outputs = result.get("outputs")
    if result.get("status") == BRAIN_ACTION_STATUS_SUCCESS and isinstance(
        outputs, Mapping
    ):
        result["outputs"] = complete_github_open_pr_project_effect(
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
        str(error.get("code") or "PROJECT_EFFECT_FAILED"),
        str(result.get("summary") or "GitHub pull request failed"),
        dict(raw_details) if isinstance(raw_details, Mapping) else {},
    )
    facts = fail_github_open_pr_project_effect(
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


def _reuse_or_reconcile_open_pr_effect(
    *,
    task_manager: Any,
    checkpoint: Any,
    existing: ProjectEffectRecord,
    scope: str,
    head_sha: str,
    args: Mapping[str, Any],
    ctx: Any,
) -> GithubOpenPrProjectEffect:
    receipt = load_project_effect_receipt(
        task_manager,
        task_id=existing.task_id,
        effect_id=existing.effect_id,
    )
    if existing.status == ProjectEffectStatus.STARTED:
        result = find_open_pr(args, ctx, head_sha=head_sha)
        if result is None:
            raise ToolRuntimeError(
                "PROJECT_EFFECT_UNCERTAIN",
                "The prior pull-request request is still uncertain; it was not repeated.",
                {
                    "reason_code": "github_open_pr_readback_not_found",
                    "project_task_id": existing.task_id,
                    "project_effect_id": existing.effect_id,
                    "project_effect_status": existing.status.value,
                    "repository_action_scope": scope,
                },
            )
        receipt = _open_pr_receipt(result, head_sha=head_sha)
        existing = existing.model_copy(
            update={
                "status": ProjectEffectStatus.SUCCEEDED,
                "result_ref": (
                    f"github:pull:{receipt['owner']}/{receipt['repo']}"
                    f"#{receipt['number']}"
                ),
                "verification_refs": ("github.open_pr:readback",),
                "non_reversible_reason": (
                    "OpenMinion does not automatically close a created pull request."
                ),
            }
        )
        save_project_effect_record(task_manager, existing, receipt=receipt)
    if receipt is None:
        raise ToolRuntimeError(
            "PROJECT_EFFECT_RECEIPT_MISSING",
            "The completed project effect has no pull-request receipt.",
            {
                "reason_code": "project_effect_receipt_missing",
                "project_task_id": existing.task_id,
                "project_effect_id": existing.effect_id,
            },
        )

    current = GithubOpenPrProjectEffect(
        task_id=existing.task_id,
        project_run_id=checkpoint.project_run.project_run_id,
        scope=scope,
        effect=existing,
        head_sha=head_sha,
    )
    ctx.github_open_pr_head_sha = head_sha
    ctx.github_open_pr_reconciled_result = _result_from_receipt(receipt)
    return current


def _open_pr_receipt(
    result: Mapping[str, Any],
    *,
    head_sha: str,
) -> dict[str, Any]:
    data = result.get("data") if isinstance(result.get("data"), Mapping) else {}
    owner = str(data.get("owner") or "")
    repo = str(data.get("repo") or "")
    source = result.get("source") if isinstance(result.get("source"), Mapping) else {}
    return {
        "owner": owner,
        "repo": repo,
        "number": int(data.get("number") or 0),
        "html_url": str(data.get("html_url") or ""),
        "head": str(data.get("head") or ""),
        "base": str(data.get("base") or ""),
        "head_sha": str(data.get("head_sha") or head_sha),
        "state": str(data.get("state") or ""),
        "provider_id": str(source.get("provider_id") or ""),
    }


def _result_from_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "data": {
            "owner": str(receipt.get("owner") or ""),
            "repo": str(receipt.get("repo") or ""),
            "number": int(receipt.get("number") or 0),
            "html_url": str(receipt.get("html_url") or ""),
            "head": str(receipt.get("head") or ""),
            "base": str(receipt.get("base") or ""),
            "head_sha": str(receipt.get("head_sha") or ""),
            "state": str(receipt.get("state") or ""),
            "reconciled": True,
        },
        "source": {"provider_id": str(receipt.get("provider_id") or "")},
    }


def _with_project_facts(
    result: Mapping[str, Any], facts: Mapping[str, Any]
) -> dict[str, Any]:
    enriched = dict(result)
    data = result.get("data") if isinstance(result.get("data"), Mapping) else {}
    enriched["data"] = {**dict(data), **dict(facts)}
    return enriched


def _is_uncertain_github_error(error: BaseException) -> bool:
    return isinstance(error, ToolRuntimeError) and (
        error.code == "REMOTE_ERROR"
        and error.details.get("reason_code") == "github_api_unreachable"
    )


__all__ = [
    "GithubOpenPrProjectEffect",
    "begin_github_open_pr_project_effect",
    "complete_github_open_pr_project_effect",
    "fail_github_open_pr_project_effect",
    "finalize_github_open_pr_project_result",
    "github_open_pr_action_scope",
]
