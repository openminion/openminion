"""Project-bound ``github.merge_pr`` execution."""

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
from openminion.tools.github.interfaces import TOOL_GITHUB_MERGE_PR
from openminion.tools.github.plugin import fetch_merge_checks, read_merge_pr
from openminion.tools.github.pull_requests import (
    require_merge_checks,
    require_merge_pr_ready,
)

from .project_github import (
    _is_uncertain_github_error,
    _raise_github_policy_denial,
    _require_project_checkpoint,
    _with_project_facts,
)
from .results import run_tool_spec


def github_merge_pr_action_scope(
    *,
    owner: str,
    repo: str,
    number: int,
    expected_head_sha: str,
    merge_method: str,
    expected_checks: list[str],
) -> str:
    checks_sha = hashlib.sha256(
        json.dumps(sorted(expected_checks), separators=(",", ":")).encode()
    ).hexdigest()
    return (
        f"repository={owner}/{repo};pr={number};head_sha={expected_head_sha};"
        f"merge_method={merge_method};checks_sha256={checks_sha}"
    )


@dataclass(frozen=True)
class GithubMergePrProjectEffect:
    checkpoint: ProjectCheckpoint
    effect: ProjectEffectRecord
    scope: str
    owner: str
    repo: str
    head_sha: str
    number: int
    merge_method: str
    expected_checks: tuple[str, ...]
    reconciled: bool = False

    def facts(self) -> dict[str, Any]:
        return {
            "project_task_id": self.effect.task_id,
            "project_run_id": self.checkpoint.project_run.project_run_id,
            "project_permission_grant_id": self.effect.approval_ref,
            "project_effect_id": self.effect.effect_id,
            "project_effect_status": self.effect.status.value,
            "repository_action_scope": self.scope,
            "repository_owner": self.owner,
            "repository_name": self.repo,
            "repository_head_sha": self.head_sha,
            "repository_pr_number": self.number,
            "repository_merge_method": self.merge_method,
            "repository_expected_checks": list(self.expected_checks),
            "project_effect_reconciled": self.reconciled,
        }


def _merge_identity(
    args: Mapping[str, Any],
) -> tuple[str, str, str, tuple[str, ...]]:
    owner = str(args.get("owner") or "")
    repo = str(args.get("repo") or "")
    number = int(args.get("number") or 0)
    head_sha = str(args.get("expected_head_sha") or "")
    merge_method = str(args.get("merge_method") or "")
    expected_checks = list(args.get("expected_checks") or [])
    scope = github_merge_pr_action_scope(
        owner=owner,
        repo=repo,
        number=number,
        expected_head_sha=head_sha,
        merge_method=merge_method,
        expected_checks=expected_checks,
    )
    checks_hash = scope.rsplit("=", 1)[-1]
    return (
        scope,
        scope,
        f"effect:github.merge_pr:{hashlib.sha256(scope.encode()).hexdigest()}",
        (
            f"github:repository:{owner}/{repo}",
            f"github:pull:{number}@{head_sha}",
            f"github:merge_method:{merge_method}",
            f"github:checks:{checks_hash}",
        ),
    )


def _begin_github_merge_pr_project_effect(
    *,
    task_manager: Any,
    task_id: str,
    actor_ref: str,
    args: Mapping[str, Any],
    ctx: Any,
) -> GithubMergePrProjectEffect:
    checkpoint = _require_project_checkpoint(task_manager, task_id)
    scope, action_key, effect_id, preconditions = _merge_identity(args)
    owner = str(args.get("owner") or "")
    repo = str(args.get("repo") or "")
    head_sha = str(args.get("expected_head_sha") or "")
    number = int(args.get("number") or 0)
    merge_method = str(args.get("merge_method") or "")
    expected_checks = list(args.get("expected_checks") or [])
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
            "The merge inputs changed since this action was recorded.",
            {
                "reason_code": replay.reason,
                "project_task_id": task_id,
                "project_effect_id": effect_id,
                "repository_action_scope": scope,
            },
        )

    preflight = read_merge_pr(args, ctx)
    require_merge_pr_ready(
        preflight,
        expected_head_sha=head_sha,
        allow_merged=existing is not None,
    )
    reconciled = False
    if existing is not None and replay.decision in {
        ProjectEffectReplayDecision.REUSE_EXISTING,
        ProjectEffectReplayDecision.BLOCK_DUPLICATE,
    }:
        was_started = existing.status == ProjectEffectStatus.STARTED
        effect = _resume_github_merge_pr_project_effect(
            task_manager,
            existing=existing,
            args=args,
            preflight=preflight,
            scope=scope,
            ctx=ctx,
        )
        reconciled = was_started and effect.status == ProjectEffectStatus.SUCCEEDED
    else:
        checks = fetch_merge_checks(args, ctx)
        require_merge_checks(
            checks,
            expected_head_sha=head_sha,
            expected_checks=expected_checks,
        )
        ctx.github_merge_pr_preflight = preflight
        ctx.github_merge_pr_checks = checks
        effect = _authorize_github_merge_pr_project_effect(
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
    started = GithubMergePrProjectEffect(
        checkpoint=checkpoint,
        effect=effect,
        scope=scope,
        owner=owner,
        repo=repo,
        head_sha=head_sha,
        number=number,
        merge_method=merge_method,
        expected_checks=tuple(expected_checks),
        reconciled=reconciled,
    )
    emit_tool_invoke_operation_for_context(
        ctx=ctx,
        operation="invoke",
        tool_name=TOOL_GITHUB_MERGE_PR,
        extra=started.facts(),
    )
    return started


def execute_github_merge_pr_project_effect(
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
    started = _begin_github_merge_pr_project_effect(
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
            tool_name=TOOL_GITHUB_MERGE_PR,
        )
        return _finalize_github_merge_pr_project_result(
            task_manager,
            started,
            result=result,
            ctx=ctx,
        )
    except ToolRuntimeError as exc:
        failure_facts = _record_merge_pr_failure(
            task_manager,
            started,
            error=exc,
            ctx=ctx,
        )
        exc.details.update(failure_facts)
        raise


def _finalize_github_merge_pr_project_result(
    task_manager: Any,
    started: GithubMergePrProjectEffect,
    *,
    result: dict[str, Any],
    ctx: Any,
) -> dict[str, Any]:
    outputs = result.get("outputs")
    if result.get("status") == BRAIN_ACTION_STATUS_SUCCESS and isinstance(
        outputs, Mapping
    ):
        receipt = _merge_pr_receipt(outputs, started)
        effect = _succeeded_merge_effect(started.effect, receipt)
        save_project_effect_record(task_manager, effect, receipt=receipt)
        completed = _effect_state(started, effect)
        emit_tool_invoke_operation_for_context(
            ctx=ctx,
            operation="completed",
            tool_name=TOOL_GITHUB_MERGE_PR,
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
        str(result.get("summary") or "GitHub pull-request merge failed"),
        {**details, "provider_error_code": provider_code},
    )
    failure_facts = _record_merge_pr_failure(
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


def _authorize_github_merge_pr_project_effect(
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
        tool_name=TOOL_GITHUB_MERGE_PR,
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
            tool_name=TOOL_GITHUB_MERGE_PR,
        )
    effect = ProjectEffectRecord(
        effect_id=effect_id,
        task_id=task_id,
        idempotency_key=action_key,
        actor_ref=actor_ref,
        capability_ref=TOOL_GITHUB_MERGE_PR,
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


def _resume_github_merge_pr_project_effect(
    task_manager: Any,
    *,
    existing: ProjectEffectRecord,
    args: Mapping[str, Any],
    preflight: Mapping[str, Any],
    scope: str,
    ctx: Any,
) -> ProjectEffectRecord:
    data = require_merge_pr_ready(
        preflight,
        expected_head_sha=str(args.get("expected_head_sha") or ""),
        allow_merged=True,
    )
    receipt = load_project_effect_receipt(
        task_manager,
        task_id=existing.task_id,
        effect_id=existing.effect_id,
    )
    if existing.status == ProjectEffectStatus.STARTED:
        if not bool(data.get("merged", False)):
            raise ToolRuntimeError(
                "UPSTREAM_ERROR",
                "The prior pull-request merge is still uncertain; it was not repeated.",
                {
                    "reason_code": "github_merge_pr_readback_not_merged",
                    "project_task_id": existing.task_id,
                    "project_effect_id": existing.effect_id,
                    "project_effect_status": existing.status.value,
                    "repository_action_scope": scope,
                },
            )
        receipt = _merge_pr_readback_receipt(preflight, args)
        existing = _succeeded_merge_effect(existing, receipt, readback=True)
        save_project_effect_record(task_manager, existing, receipt=receipt)
    if receipt is None:
        raise ToolRuntimeError(
            "INTERNAL_ERROR",
            "The completed project effect has no pull-request merge receipt.",
            {
                "reason_code": "project_effect_receipt_missing",
                "project_task_id": existing.task_id,
                "project_effect_id": existing.effect_id,
            },
        )
    ctx.github_merge_pr_reconciled_result = _merge_pr_result_from_receipt(receipt)
    return existing


def _record_merge_pr_failure(
    task_manager: Any,
    started: GithubMergePrProjectEffect,
    *,
    error: BaseException,
    ctx: Any,
) -> dict[str, Any]:
    uncertain = _merge_failure_is_uncertain(error)
    effect = started.effect
    if not uncertain:
        effect = effect.model_copy(update={"status": ProjectEffectStatus.FAILED})
        save_project_effect_record(task_manager, effect)
    failed = _effect_state(started, effect)
    facts = {**failed.facts(), "project_effect_uncertain": uncertain}
    emit_tool_invoke_operation_for_context(
        ctx=ctx,
        operation="completed",
        tool_name=TOOL_GITHUB_MERGE_PR,
        status="error",
        error_code=(
            "PROJECT_EFFECT_UNCERTAIN" if uncertain else "PROJECT_EFFECT_FAILED"
        ),
        extra=facts,
    )
    return facts


def _merge_failure_is_uncertain(error: BaseException) -> bool:
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


def _effect_state(
    started: GithubMergePrProjectEffect,
    effect: ProjectEffectRecord,
) -> GithubMergePrProjectEffect:
    return GithubMergePrProjectEffect(
        checkpoint=started.checkpoint,
        effect=effect,
        scope=started.scope,
        owner=started.owner,
        repo=started.repo,
        head_sha=started.head_sha,
        number=started.number,
        merge_method=started.merge_method,
        expected_checks=started.expected_checks,
        reconciled=started.reconciled,
    )


def _succeeded_merge_effect(
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
                f"@{receipt['merge_commit_sha']}"
            ),
            "verification_refs": (
                ("github.merge_pr:readback",) if readback else effect.verification_refs
            ),
            "non_reversible_reason": (
                "OpenMinion does not automatically revert a merged pull request."
            ),
        }
    )


def _merge_pr_receipt(
    result: Mapping[str, Any],
    started: GithubMergePrProjectEffect,
) -> dict[str, Any]:
    raw_data = result.get("data")
    if not isinstance(raw_data, Mapping) or raw_data.get("merged") is not True:
        raise ToolRuntimeError(
            "INVALID_RESPONSE",
            "GitHub merge response did not confirm the merge.",
            {"reason_code": "github_merge_pr_result_bad_result"},
        )
    raw_source = result.get("source")
    source = raw_source if isinstance(raw_source, Mapping) else {}
    actual = (
        str(raw_data.get("owner") or ""),
        str(raw_data.get("repo") or ""),
        int(raw_data.get("number") or 0),
        str(raw_data.get("head_sha") or ""),
        str(raw_data.get("merge_method") or ""),
    )
    expected = (
        started.owner,
        started.repo,
        started.number,
        started.head_sha,
        started.merge_method,
    )
    if actual != expected:
        raise ToolRuntimeError(
            "INVALID_RESPONSE",
            "GitHub merge response did not match the approved action.",
            {"reason_code": "github_merge_pr_result_mismatch"},
        )
    merge_commit_sha = str(raw_data.get("merge_commit_sha") or "")
    provider_id = str(source.get("provider_id") or "")
    if not merge_commit_sha or not provider_id:
        raise ToolRuntimeError(
            "INVALID_RESPONSE",
            "GitHub merge response omitted required receipt fields.",
            {"reason_code": "github_merge_pr_result_incomplete"},
        )
    return {
        "owner": started.owner,
        "repo": started.repo,
        "number": started.number,
        "merged": True,
        "message": str(raw_data.get("message") or ""),
        "head_sha": started.head_sha,
        "merge_method": started.merge_method,
        "merge_commit_sha": merge_commit_sha,
        "provider_id": provider_id,
    }


def _merge_pr_readback_receipt(
    result: Mapping[str, Any],
    args: Mapping[str, Any],
) -> dict[str, Any]:
    data = require_merge_pr_ready(
        result,
        expected_head_sha=str(args.get("expected_head_sha") or ""),
        allow_merged=True,
    )
    actual = (
        str(data.get("owner") or ""),
        str(data.get("repo") or ""),
        int(data.get("number") or 0),
    )
    expected = (
        str(args.get("owner") or ""),
        str(args.get("repo") or ""),
        int(args.get("number") or 0),
    )
    if actual != expected:
        raise ToolRuntimeError(
            "INVALID_RESPONSE",
            "GitHub merge readback did not match the approved action.",
            {"reason_code": "github_merge_pr_result_mismatch"},
        )
    merge_commit_sha = str(data.get("merge_commit_sha") or "")
    raw_source = result.get("source")
    source = raw_source if isinstance(raw_source, Mapping) else {}
    provider_id = str(source.get("provider_id") or "")
    if not merge_commit_sha or not provider_id:
        raise ToolRuntimeError(
            "INVALID_RESPONSE",
            "GitHub merge readback omitted required receipt fields.",
            {"reason_code": "github_merge_pr_result_incomplete"},
        )
    return {
        "owner": str(data.get("owner") or ""),
        "repo": str(data.get("repo") or ""),
        "number": int(data.get("number") or 0),
        "merged": True,
        "message": "Reconciled from pull-request readback.",
        "head_sha": str(data.get("head_sha") or ""),
        "merge_method": str(args.get("merge_method") or ""),
        "merge_commit_sha": merge_commit_sha,
        "provider_id": provider_id,
    }


def _merge_pr_result_from_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "data": {
            "owner": str(receipt.get("owner") or ""),
            "repo": str(receipt.get("repo") or ""),
            "number": int(receipt.get("number") or 0),
            "merged": True,
            "message": str(receipt.get("message") or ""),
            "head_sha": str(receipt.get("head_sha") or ""),
            "merge_method": str(receipt.get("merge_method") or ""),
            "merge_commit_sha": str(receipt.get("merge_commit_sha") or ""),
            "reconciled": True,
        },
        "source": {"provider_id": str(receipt.get("provider_id") or "")},
    }


__all__ = [
    "execute_github_merge_pr_project_effect",
    "github_merge_pr_action_scope",
]
