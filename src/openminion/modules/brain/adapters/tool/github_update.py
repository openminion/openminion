"""Project-bound ``github.update_pr`` execution."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

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
from openminion.modules.tool.diagnostics.events import (
    emit_tool_invoke_operation_for_context,
)
from openminion.modules.tool import RuntimeContext, ToolSpec
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.tools.github.interfaces import TOOL_GITHUB_UPDATE_PR
from openminion.tools.github.plugin import read_update_pr

from .project_github import (
    _is_uncertain_github_error,
    _raise_github_policy_denial,
    _require_project_checkpoint,
    _with_project_facts,
)
from .results import run_tool_spec


def github_update_pr_action_scope(
    *,
    owner: str,
    repo: str,
    number: int,
    head_sha: str,
    title: str | None,
    body: str | None,
) -> str:
    update = {
        key: value
        for key, value in (("title", title), ("body", body))
        if value is not None
    }
    update_sha = hashlib.sha256(
        json.dumps(update, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return (
        f"repository={owner}/{repo};pr={number};head_sha={head_sha};"
        f"update_sha256={update_sha}"
    )


@dataclass(frozen=True)
class GithubUpdatePrProjectEffect:
    checkpoint: ProjectCheckpoint
    effect: ProjectEffectRecord
    scope: str
    head_sha: str
    number: int
    reconciled: bool = False

    def facts(self) -> dict[str, Any]:
        return {
            "project_task_id": self.effect.task_id,
            "project_run_id": self.checkpoint.project_run.project_run_id,
            "project_permission_grant_id": self.effect.approval_ref,
            "project_effect_id": self.effect.effect_id,
            "project_effect_status": self.effect.status.value,
            "repository_action_scope": self.scope,
            "repository_head_sha": self.head_sha,
            "repository_pr_number": self.number,
            "project_effect_reconciled": self.reconciled,
        }


def _update_pr_identity(
    args: Mapping[str, Any],
    ctx: Any,
) -> tuple[Mapping[str, Any], str, str, str, tuple[str, ...]]:
    owner = str(args.get("owner") or "")
    repo = str(args.get("repo") or "")
    number = int(args.get("number") or 0)
    preflight = read_update_pr(args, ctx)
    data = _update_pr_data(preflight)
    head_sha = str(data.get("head_sha") or "")
    if not head_sha:
        raise ToolRuntimeError(
            "INVALID_RESPONSE",
            "GitHub update preflight omitted the pull-request head SHA.",
            {"reason_code": "github_update_pr_head_sha_missing"},
        )
    scope = github_update_pr_action_scope(
        owner=owner,
        repo=repo,
        number=number,
        head_sha=head_sha,
        title=args.get("title"),
        body=args.get("body"),
    )
    action_key = scope
    ctx.github_update_pr_preflight = preflight
    return (
        preflight,
        scope,
        action_key,
        f"effect:github.update_pr:{action_key}",
        (
            f"github:repository:{owner}/{repo}",
            f"github:pull:{number}@{head_sha}",
            f"github:update:{scope.rsplit('=', 1)[-1]}",
        ),
    )


def _begin_github_update_pr_project_effect(
    *,
    task_manager: Any,
    task_id: str,
    actor_ref: str,
    args: Mapping[str, Any],
    ctx: Any,
) -> GithubUpdatePrProjectEffect:
    checkpoint = _require_project_checkpoint(task_manager, task_id)
    number = int(args.get("number") or 0)
    preflight, scope, action_key, effect_id, preconditions = _update_pr_identity(
        args, ctx
    )
    data = _update_pr_data(preflight)
    head_sha = str(data.get("head_sha") or "")
    existing = load_project_effect_record(
        task_manager,
        task_id=task_id,
        effect_id=effect_id,
    )
    replay = evaluate_project_effect_replay(
        existing,
        idempotency_key=action_key,
        precondition_refs=preconditions,
    )
    if replay.decision == ProjectEffectReplayDecision.BLOCK_STALE_PRECONDITION:
        raise ToolRuntimeError(
            "INVALID_REQUEST",
            "The pull-request state changed since this action was recorded.",
            {
                "reason_code": replay.reason,
                "project_task_id": task_id,
                "project_effect_id": effect_id,
                "repository_action_scope": scope,
            },
        )
    reconciled = existing is not None and replay.decision in {
        ProjectEffectReplayDecision.REUSE_EXISTING,
        ProjectEffectReplayDecision.BLOCK_DUPLICATE,
    }
    if reconciled:
        effect = _resume_github_update_pr_project_effect(
            task_manager,
            existing=existing,
            args=args,
            preflight=preflight,
            scope=scope,
            ctx=ctx,
        )
    else:
        effect = _authorize_github_update_pr_project_effect(
            task_manager,
            task_id=task_id,
            checkpoint=checkpoint,
            effect_id=effect_id,
            action_key=action_key,
            actor_ref=actor_ref,
            preconditions=preconditions,
            scope=scope,
            head_sha=head_sha,
            number=number,
            ctx=ctx,
        )
    started = GithubUpdatePrProjectEffect(
        checkpoint=checkpoint,
        effect=effect,
        scope=scope,
        head_sha=head_sha,
        number=number,
        reconciled=reconciled,
    )
    emit_tool_invoke_operation_for_context(
        ctx=ctx,
        operation="invoke",
        tool_name=TOOL_GITHUB_UPDATE_PR,
        extra=started.facts(),
    )
    return started


def execute_github_update_pr_project_effect(
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
    started = _begin_github_update_pr_project_effect(
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
            tool_name=TOOL_GITHUB_UPDATE_PR,
        )
        return _finalize_github_update_pr_project_result(
            task_manager,
            started,
            result=result,
            args=args,
            ctx=ctx,
        )
    except ToolRuntimeError as exc:
        failure_facts = _record_update_pr_failure(
            task_manager,
            started,
            error=exc,
            ctx=ctx,
        )
        exc.details.update(failure_facts)
        raise


def _finalize_github_update_pr_project_result(
    task_manager: Any,
    started: GithubUpdatePrProjectEffect,
    *,
    result: dict[str, Any],
    args: Mapping[str, Any],
    ctx: Any,
) -> dict[str, Any]:
    outputs = result.get("outputs")
    if result.get("status") == BRAIN_ACTION_STATUS_SUCCESS and isinstance(
        outputs, Mapping
    ):
        receipt = _update_pr_receipt(
            outputs,
            args=args,
            head_sha=started.head_sha,
        )
        effect = _succeeded_update_effect(started.effect, receipt)
        save_project_effect_record(task_manager, effect, receipt=receipt)
        completed = GithubUpdatePrProjectEffect(
            checkpoint=started.checkpoint,
            effect=effect,
            scope=started.scope,
            head_sha=started.head_sha,
            number=started.number,
            reconciled=started.reconciled,
        )
        emit_tool_invoke_operation_for_context(
            ctx=ctx,
            operation="completed",
            tool_name=TOOL_GITHUB_UPDATE_PR,
            extra=completed.facts(),
        )
        result["outputs"] = _with_project_facts(outputs, completed.facts())
        return result
    raw_error = result.get("error")
    error = raw_error if isinstance(raw_error, Mapping) else {}
    raw_details = error.get("details")
    details = dict(raw_details) if isinstance(raw_details, Mapping) else {}
    provider_code = str(error.get("code") or "")
    failure = ToolRuntimeError(
        "UPSTREAM_ERROR",
        str(result.get("summary") or "GitHub pull-request update failed"),
        {**details, "provider_error_code": provider_code},
    )
    failure_facts = _record_update_pr_failure(
        task_manager,
        started,
        error=failure,
        ctx=ctx,
    )
    if isinstance(raw_error, dict):
        raw_error["code"] = "UPSTREAM_ERROR"
        raw_error["details"] = {
            **details,
            "provider_error_code": provider_code,
            **failure_facts,
        }
    return result


def _authorize_github_update_pr_project_effect(
    task_manager: Any,
    *,
    task_id: str,
    checkpoint: ProjectCheckpoint,
    effect_id: str,
    action_key: str,
    actor_ref: str,
    preconditions: tuple[str, ...],
    scope: str,
    head_sha: str,
    number: int,
    ctx: Any,
) -> ProjectEffectRecord:
    permission = evaluate_project_permission(
        task_manager,
        task_id=task_id,
        tool_name=TOOL_GITHUB_UPDATE_PR,
        scope=scope,
    )
    if not permission.allowed:
        _raise_github_policy_denial(
            checkpoint=checkpoint,
            permission=permission,
            task_id=task_id,
            scope=scope,
            head_sha=head_sha,
            number=number,
            ctx=ctx,
            tool_name=TOOL_GITHUB_UPDATE_PR,
        )
    effect = ProjectEffectRecord(
        effect_id=effect_id,
        task_id=task_id,
        idempotency_key=action_key,
        actor_ref=actor_ref,
        capability_ref=TOOL_GITHUB_UPDATE_PR,
        precondition_refs=preconditions,
        approval_ref=permission.grant_id,
    )
    save_project_effect_record(task_manager, effect)
    consume_project_permission_grant(
        task_manager,
        task_id=task_id,
        grant_id=str(permission.grant_id or ""),
    )
    return effect


def _resume_github_update_pr_project_effect(
    task_manager: Any,
    *,
    existing: ProjectEffectRecord,
    args: Mapping[str, Any],
    preflight: Mapping[str, Any],
    scope: str,
    ctx: Any,
) -> ProjectEffectRecord:
    data = _update_pr_data(preflight)
    receipt = load_project_effect_receipt(
        task_manager,
        task_id=existing.task_id,
        effect_id=existing.effect_id,
    )
    if existing.status == ProjectEffectStatus.STARTED:
        applied = all(
            args.get(field) is None or str(data.get(field) or "") == args.get(field)
            for field in ("title", "body")
        )
        if not applied:
            raise ToolRuntimeError(
                "UPSTREAM_ERROR",
                "The prior pull-request update is still uncertain; it was not repeated.",
                {
                    "reason_code": "github_update_pr_readback_not_applied",
                    "project_task_id": existing.task_id,
                    "project_effect_id": existing.effect_id,
                    "project_effect_status": existing.status.value,
                    "repository_action_scope": scope,
                },
            )
        receipt = _update_pr_receipt(
            preflight,
            args=args,
            head_sha=str(data["head_sha"]),
        )
        existing = _succeeded_update_effect(existing, receipt, readback=True)
        save_project_effect_record(task_manager, existing, receipt=receipt)
    if receipt is None:
        raise ToolRuntimeError(
            "INTERNAL_ERROR",
            "The completed project effect has no pull-request receipt.",
            {
                "reason_code": "project_effect_receipt_missing",
                "project_task_id": existing.task_id,
                "project_effect_id": existing.effect_id,
            },
        )
    ctx.github_update_pr_reconciled_result = _update_pr_result_from_receipt(receipt)
    return existing


def _update_pr_data(result: Mapping[str, Any]) -> Mapping[str, Any]:
    data = result.get("data")
    if not isinstance(data, Mapping):
        raise ToolRuntimeError(
            "INVALID_RESPONSE",
            "GitHub update preflight omitted pull-request data.",
            {"reason_code": "github_update_pr_preflight_bad_result"},
        )
    return data


def _record_update_pr_failure(
    task_manager: Any,
    started: GithubUpdatePrProjectEffect,
    *,
    error: BaseException,
    ctx: Any,
) -> dict[str, Any]:
    uncertain = _update_pr_failure_is_uncertain(error)
    effect = started.effect
    if not uncertain:
        effect = effect.model_copy(update={"status": ProjectEffectStatus.FAILED})
        save_project_effect_record(task_manager, effect)
    failed = GithubUpdatePrProjectEffect(
        checkpoint=started.checkpoint,
        effect=effect,
        scope=started.scope,
        head_sha=started.head_sha,
        number=started.number,
        reconciled=started.reconciled,
    )
    facts = {
        **failed.facts(),
        "project_effect_uncertain": uncertain,
    }
    emit_tool_invoke_operation_for_context(
        ctx=ctx,
        operation="completed",
        tool_name=TOOL_GITHUB_UPDATE_PR,
        status="error",
        error_code=(
            "PROJECT_EFFECT_UNCERTAIN" if uncertain else "PROJECT_EFFECT_FAILED"
        ),
        extra=facts,
    )
    return facts


def _update_pr_failure_is_uncertain(error: BaseException) -> bool:
    if _is_uncertain_github_error(error):
        return True
    if not isinstance(error, ToolRuntimeError):
        return False
    provider_code = str(error.details.get("provider_error_code") or "")
    status_code = error.details.get("status_code")
    return (
        error.code == "INVALID_RESPONSE"
        or provider_code == "INVALID_RESPONSE"
        or (
            error.details.get("reason_code") == "github_api_error"
            and isinstance(status_code, int)
            and 500 <= status_code < 600
        )
    )


def _succeeded_update_effect(
    effect: ProjectEffectRecord,
    receipt: Mapping[str, Any],
    *,
    readback: bool = False,
) -> ProjectEffectRecord:
    return effect.model_copy(
        update={
            "status": ProjectEffectStatus.SUCCEEDED,
            "result_ref": (
                f"github:pull:{receipt['owner']}/{receipt['repo']}#{receipt['number']}"
            ),
            "verification_refs": (
                ("github.update_pr:readback",) if readback else effect.verification_refs
            ),
            "non_reversible_reason": (
                "OpenMinion does not automatically restore a prior pull-request title or body."
            ),
        }
    )


def _update_pr_receipt(
    result: Mapping[str, Any],
    *,
    args: Mapping[str, Any],
    head_sha: str,
) -> dict[str, Any]:
    raw_data = result.get("data")
    raw_source = result.get("source")
    string_fields = (
        "owner",
        "repo",
        "html_url",
        "title",
        "body",
        "state",
        "head_sha",
    )
    valid_shape = (
        result.get("ok") is True
        and isinstance(raw_data, Mapping)
        and isinstance(raw_source, Mapping)
        and all(isinstance(raw_data.get(field), str) for field in string_fields)
        and isinstance(raw_data.get("number"), int)
        and not isinstance(raw_data.get("number"), bool)
        and isinstance(raw_source.get("provider_id"), str)
    )
    if not valid_shape:
        raise ToolRuntimeError(
            "INVALID_RESPONSE",
            "GitHub update returned an invalid pull-request result.",
            {"reason_code": "github_update_pr_result_invalid"},
        )
    data = cast(Mapping[str, Any], raw_data)
    source = cast(Mapping[str, Any], raw_source)
    expected_update_matches = all(
        args.get(field) is None or data[field] == args[field]
        for field in ("title", "body")
    )
    if (
        not all(
            data[field] for field in ("owner", "repo", "html_url", "title", "head_sha")
        )
        or not source["provider_id"]
        or data["owner"] != args.get("owner")
        or data["repo"] != args.get("repo")
        or data["number"] != args.get("number")
        or data["state"] != "open"
        or data["head_sha"] != head_sha
        or not expected_update_matches
    ):
        raise ToolRuntimeError(
            "INVALID_RESPONSE",
            "GitHub update returned a mismatched pull-request result.",
            {"reason_code": "github_update_pr_result_mismatch"},
        )
    return {
        "owner": data["owner"],
        "repo": data["repo"],
        "number": data["number"],
        "html_url": data["html_url"],
        "title": data["title"],
        "body": data["body"],
        "state": data["state"],
        "head_sha": data["head_sha"],
        "provider_id": source["provider_id"],
    }


def _update_pr_result_from_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "data": {
            "owner": str(receipt.get("owner") or ""),
            "repo": str(receipt.get("repo") or ""),
            "number": int(receipt.get("number") or 0),
            "html_url": str(receipt.get("html_url") or ""),
            "title": str(receipt.get("title") or ""),
            "body": str(receipt.get("body") or ""),
            "state": str(receipt.get("state") or ""),
            "head_sha": str(receipt.get("head_sha") or ""),
            "reconciled": True,
        },
        "source": {"provider_id": str(receipt.get("provider_id") or "")},
    }


__all__ = [
    "execute_github_update_pr_project_effect",
    "github_update_pr_action_scope",
]
