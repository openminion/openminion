"""Project-bound GitHub workflow dispatch."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from openminion.modules.brain.constants import BRAIN_ACTION_STATUS_SUCCESS
from openminion.modules.task.project.effects import (
    ProjectEffectRecord,
    ProjectEffectReplayDecision,
    ProjectEffectStatus,
    evaluate_project_effect_replay,
    load_project_effect_receipt,
    load_project_effect_record,
    save_project_effect_record,
)
from openminion.modules.task.project.models import ProjectCheckpoint
from openminion.modules.task.project.policy import (
    consume_project_permission_grant,
    evaluate_project_permission,
)
from openminion.modules.tool import RuntimeContext, ToolSpec
from openminion.modules.tool.diagnostics.events import (
    emit_tool_invoke_operation_for_context,
)
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.tools.github.interfaces import TOOL_GITHUB_DISPATCH_WORKFLOW
from openminion.tools.github.plugin import read_dispatch_workflow

from .project_github import (
    _is_uncertain_github_error,
    _raise_github_policy_denial,
    _require_project_checkpoint,
    _with_project_facts,
)
from .results import run_tool_spec


def github_workflow_action_scope(args: Mapping[str, Any]) -> str:
    inputs_sha = hashlib.sha256(
        json.dumps(
            dict(args.get("inputs") or {}), sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    return (
        f"repository={args.get('owner')}/{args.get('repo')};"
        f"workflow={args.get('workflow')};ref={args.get('ref')};"
        f"request={args.get('request_id')};target={args.get('target')};"
        f"inputs_sha256={inputs_sha}"
    )


@dataclass(frozen=True)
class GithubWorkflowProjectEffect:
    checkpoint: ProjectCheckpoint
    effect: ProjectEffectRecord
    scope: str
    owner: str
    repo: str
    workflow: str
    ref: str
    request_id: str
    target: str
    reconciled: bool = False

    def facts(self) -> dict[str, Any]:
        return {
            "project_task_id": self.effect.task_id,
            "project_run_id": self.checkpoint.project_run.project_run_id,
            "project_permission_grant_id": self.effect.approval_ref,
            "project_effect_id": self.effect.effect_id,
            "project_effect_status": self.effect.status.value,
            "project_effect_reconciled": self.reconciled,
            "repository_action_scope": self.scope,
            "repository_owner": self.owner,
            "repository_name": self.repo,
            "repository_workflow": self.workflow,
            "repository_ref": self.ref,
            "repository_request_id": self.request_id,
            "repository_publish_target": self.target,
        }


def execute_github_workflow_project_effect(
    *,
    task_manager: Any | None,
    task_id: str,
    actor_ref: str,
    args: dict[str, Any],
    ctx: RuntimeContext,
    spec: ToolSpec,
    start_time: float,
    background_write_authorized: bool,
) -> dict[str, Any]:
    if task_manager is None:
        raise ToolRuntimeError(
            "INVALID_REQUEST",
            "Project tool execution requires the task manager.",
            {
                "reason_code": "project_task_manager_unavailable",
                "project_task_id": task_id,
            },
        )
    started = _begin_workflow_effect(
        task_manager=task_manager,
        task_id=task_id,
        actor_ref=actor_ref,
        args=args,
        ctx=ctx,
    )
    try:
        result = run_tool_spec(
            spec=spec,
            validated_args=args,
            context=ctx,
            start_time=start_time,
            background_write_authorized=background_write_authorized,
            tool_name=TOOL_GITHUB_DISPATCH_WORKFLOW,
        )
        return _finalize_workflow_result(task_manager, started, result=result, ctx=ctx)
    except ToolRuntimeError as exc:
        exc.details.update(
            _record_workflow_failure(task_manager, started, error=exc, ctx=ctx)
        )
        raise


def _begin_workflow_effect(
    *,
    task_manager: Any,
    task_id: str,
    actor_ref: str,
    args: Mapping[str, Any],
    ctx: Any,
) -> GithubWorkflowProjectEffect:
    checkpoint = _require_project_checkpoint(task_manager, task_id)
    scope = github_workflow_action_scope(args)
    effect_id = (
        f"effect:github.dispatch_workflow:{hashlib.sha256(scope.encode()).hexdigest()}"
    )
    preconditions = _workflow_preconditions(args)
    existing = load_project_effect_record(
        task_manager, task_id=task_id, effect_id=effect_id
    )
    replay = evaluate_project_effect_replay(
        existing, idempotency_key=scope, precondition_refs=preconditions
    )
    if replay.decision == ProjectEffectReplayDecision.BLOCK_STALE_PRECONDITION:
        raise ToolRuntimeError(
            "INVALID_REQUEST",
            "The workflow request changed since this action was recorded.",
            {"reason_code": replay.reason, "project_effect_id": effect_id},
        )
    reconciled = False
    if existing is not None and replay.decision in {
        ProjectEffectReplayDecision.REUSE_EXISTING,
        ProjectEffectReplayDecision.BLOCK_DUPLICATE,
    }:
        was_started = existing.status == ProjectEffectStatus.STARTED
        effect = _resume_workflow_effect(
            task_manager, existing=existing, args=args, scope=scope, ctx=ctx
        )
        reconciled = was_started and effect.status == ProjectEffectStatus.SUCCEEDED
    else:
        permission = evaluate_project_permission(
            task_manager,
            task_id=task_id,
            tool_name=TOOL_GITHUB_DISPATCH_WORKFLOW,
            scope=scope,
        )
        if not permission.allowed:
            _raise_github_policy_denial(
                checkpoint=checkpoint,
                permission=permission,
                task_id=task_id,
                scope=scope,
                head_sha=str(args.get("ref") or ""),
                ctx=ctx,
                tool_name=TOOL_GITHUB_DISPATCH_WORKFLOW,
            )
        effect = ProjectEffectRecord(
            effect_id=effect_id,
            task_id=task_id,
            idempotency_key=scope,
            actor_ref=actor_ref,
            capability_ref=TOOL_GITHUB_DISPATCH_WORKFLOW,
            precondition_refs=preconditions,
            approval_ref=permission.grant_id,
        )
        save_project_effect_record(task_manager, effect)
        consume_project_permission_grant(
            task_manager, task_id=task_id, grant_id=str(permission.grant_id or "")
        )
    started = _workflow_state(checkpoint, effect, args, scope, reconciled=reconciled)
    emit_tool_invoke_operation_for_context(
        ctx=ctx,
        operation="invoke",
        tool_name=TOOL_GITHUB_DISPATCH_WORKFLOW,
        extra=started.facts(),
    )
    return started


def _workflow_state(
    checkpoint: ProjectCheckpoint,
    effect: ProjectEffectRecord,
    args: Mapping[str, Any],
    scope: str,
    *,
    reconciled: bool = False,
) -> GithubWorkflowProjectEffect:
    return GithubWorkflowProjectEffect(
        checkpoint=checkpoint,
        effect=effect,
        scope=scope,
        owner=str(args.get("owner") or ""),
        repo=str(args.get("repo") or ""),
        workflow=str(args.get("workflow") or ""),
        ref=str(args.get("ref") or ""),
        request_id=str(args.get("request_id") or ""),
        target=str(args.get("target") or ""),
        reconciled=reconciled,
    )


def _workflow_preconditions(args: Mapping[str, Any]) -> tuple[str, ...]:
    scope = github_workflow_action_scope(args)
    refs = [
        f"github:repository:{args.get('owner')}/{args.get('repo')}",
        f"github:workflow:{args.get('workflow')}@{args.get('ref')}",
        f"github:workflow_request:{args.get('request_id')}",
        f"github:workflow_inputs:{scope.rsplit('=', 1)[-1]}",
    ]
    if args.get("target") == "pypi":
        refs.append("github:final_release_approval:pypi")
    return tuple(refs)


def _resume_workflow_effect(
    task_manager: Any,
    *,
    existing: ProjectEffectRecord,
    args: Mapping[str, Any],
    scope: str,
    ctx: Any,
) -> ProjectEffectRecord:
    receipt = load_project_effect_receipt(
        task_manager, task_id=existing.task_id, effect_id=existing.effect_id
    )
    if existing.status == ProjectEffectStatus.STARTED:
        readback = read_dispatch_workflow(args, ctx)
        receipt = _workflow_receipt(readback, args)
        if receipt["match"] != "exact":
            raise ToolRuntimeError(
                "UPSTREAM_ERROR",
                "The prior workflow dispatch is unresolved; it was not repeated.",
                {
                    "reason_code": f"github_workflow_readback_{receipt['match']}",
                    "project_effect_uncertain": True,
                    "project_effect_id": existing.effect_id,
                    "repository_action_scope": scope,
                },
            )
        existing = existing.model_copy(
            update={
                "status": ProjectEffectStatus.SUCCEEDED,
                "result_ref": f"github:workflow_run:{receipt['run_id']}",
                "verification_refs": ("github.dispatch_workflow:readback",),
                "non_reversible_reason": "OpenMinion does not cancel a dispatched workflow automatically.",
            }
        )
        save_project_effect_record(task_manager, existing, receipt=receipt)
    if receipt is None:
        raise ToolRuntimeError(
            "INTERNAL_ERROR",
            "The completed workflow effect has no receipt.",
            {
                "reason_code": "project_effect_receipt_missing",
                "project_effect_id": existing.effect_id,
            },
        )
    ctx.github_dispatch_workflow_reconciled_result = _workflow_result_from_receipt(
        receipt
    )
    return existing


def _finalize_workflow_result(
    task_manager: Any,
    started: GithubWorkflowProjectEffect,
    *,
    result: dict[str, Any],
    ctx: Any,
) -> dict[str, Any]:
    outputs = result.get("outputs")
    if result.get("status") == BRAIN_ACTION_STATUS_SUCCESS and isinstance(
        outputs, Mapping
    ):
        receipt = _workflow_receipt(
            outputs,
            {
                "owner": started.owner,
                "repo": started.repo,
                "workflow": started.workflow,
                "ref": started.ref,
                "request_id": started.request_id,
                "target": started.target,
            },
        )
        effect = started.effect
        uncertain = receipt["match"] != "exact"
        if not uncertain:
            effect = effect.model_copy(
                update={
                    "status": ProjectEffectStatus.SUCCEEDED,
                    "result_ref": f"github:workflow_run:{receipt['run_id']}",
                    "non_reversible_reason": "OpenMinion does not cancel a dispatched workflow automatically.",
                }
            )
        save_project_effect_record(task_manager, effect, receipt=receipt)
        completed = GithubWorkflowProjectEffect(
            **{**started.__dict__, "effect": effect}
        )
        facts = {**completed.facts(), "project_effect_uncertain": uncertain}
        emit_tool_invoke_operation_for_context(
            ctx=ctx,
            operation="completed",
            tool_name=TOOL_GITHUB_DISPATCH_WORKFLOW,
            status="error" if uncertain else "ok",
            error_code="PROJECT_EFFECT_UNCERTAIN" if uncertain else None,
            extra=facts,
        )
        result["outputs"] = _with_project_facts(outputs, facts)
        return result
    failure = ToolRuntimeError(
        "UPSTREAM_ERROR",
        str(result.get("summary") or "GitHub workflow dispatch failed"),
    )
    facts = _record_workflow_failure(task_manager, started, error=failure, ctx=ctx)
    raw_error = result.get("error")
    if isinstance(raw_error, dict):
        raw_error["code"] = "UPSTREAM_ERROR"
        raw_error["details"] = {**dict(raw_error.get("details") or {}), **facts}
    return result


def _record_workflow_failure(
    task_manager: Any,
    started: GithubWorkflowProjectEffect,
    *,
    error: BaseException,
    ctx: Any,
) -> dict[str, Any]:
    status_code = error.details.get("status_code") if isinstance(
        error, ToolRuntimeError
    ) else None
    uncertain = _is_uncertain_github_error(error) or (
        isinstance(error, ToolRuntimeError)
        and (
            error.code == "INVALID_RESPONSE"
            or (
                error.details.get("reason_code") == "github_api_error"
                and isinstance(status_code, int)
                and 500 <= status_code < 600
            )
        )
    )
    effect = started.effect
    if not uncertain:
        effect = effect.model_copy(update={"status": ProjectEffectStatus.FAILED})
        save_project_effect_record(task_manager, effect)
    current = GithubWorkflowProjectEffect(**{**started.__dict__, "effect": effect})
    facts = {**current.facts(), "project_effect_uncertain": uncertain}
    emit_tool_invoke_operation_for_context(
        ctx=ctx,
        operation="completed",
        tool_name=TOOL_GITHUB_DISPATCH_WORKFLOW,
        status="error",
        error_code="PROJECT_EFFECT_UNCERTAIN" if uncertain else "PROJECT_EFFECT_FAILED",
        extra=facts,
    )
    return facts


def _workflow_receipt(
    result: Mapping[str, Any], args: Mapping[str, Any]
) -> dict[str, Any]:
    data = result.get("data")
    source = result.get("source")
    if not isinstance(data, Mapping) or not isinstance(source, Mapping):
        raise ToolRuntimeError(
            "INVALID_RESPONSE",
            "GitHub workflow result omitted data or provider identity.",
            {"reason_code": "github_workflow_result_invalid"},
        )
    expected = {
        "owner": str(args.get("owner") or ""),
        "repo": str(args.get("repo") or ""),
        "workflow": str(args.get("workflow") or ""),
        "ref": str(args.get("ref") or ""),
        "request_id": str(args.get("request_id") or ""),
    }
    if any(str(data.get(key) or "") != value for key, value in expected.items()):
        raise ToolRuntimeError(
            "INVALID_RESPONSE",
            "GitHub workflow result does not match the approved request.",
            {"reason_code": "github_workflow_result_mismatch"},
        )
    runs = data.get("runs")
    if not isinstance(runs, list):
        raise ToolRuntimeError(
            "INVALID_RESPONSE",
            "GitHub workflow result omitted bounded run facts.",
            {"reason_code": "github_workflow_result_invalid"},
        )
    match = str(data.get("match") or "")
    if (
        match not in {"exact", "not_found", "ambiguous"}
        or (match == "not_found" and runs)
        or (match in {"exact", "ambiguous"} and not runs)
    ):
        raise ToolRuntimeError(
            "INVALID_RESPONSE",
            "GitHub workflow result contains inconsistent match facts.",
            {"reason_code": "github_workflow_result_invalid"},
        )
    run = (
        runs[0]
        if match == "exact" and len(runs) == 1 and isinstance(runs[0], Mapping)
        else {}
    )
    run_id = run.get("run_id")
    provider_id = str(source.get("provider_id") or "")
    if match == "exact" and (not isinstance(run_id, int) or not provider_id):
        raise ToolRuntimeError(
            "INVALID_RESPONSE",
            "GitHub workflow exact match omitted run or provider identity.",
            {"reason_code": "github_workflow_result_incomplete"},
        )
    return {
        **expected,
        "target": str(args.get("target") or ""),
        "match": match,
        "run_id": run_id,
        "run": dict(run),
        "provider_id": provider_id,
        "truncated": bool(data.get("truncated")),
    }


def _workflow_result_from_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "data": {
            key: receipt.get(key)
            for key in (
                "owner",
                "repo",
                "workflow",
                "ref",
                "request_id",
                "target",
                "match",
            )
        }
        | {"runs": [dict(receipt.get("run") or {})], "reconciled": True},
        "source": {"provider_id": str(receipt.get("provider_id") or "")},
    }


__all__ = ["execute_github_workflow_project_effect", "github_workflow_action_scope"]
