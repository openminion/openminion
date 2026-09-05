"""Project-bound GitHub release creation."""

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
from openminion.tools.github.interfaces import TOOL_GITHUB_CREATE_RELEASE
from openminion.tools.github.plugin import read_release

from .project_github import (
    _is_uncertain_github_error,
    _raise_github_policy_denial,
    _require_project_checkpoint,
    _with_project_facts,
)
from .results import run_tool_spec


def github_release_action_scope(args: Mapping[str, Any]) -> str:
    content = {key: args.get(key) for key in ("title", "notes", "draft", "prerelease")}
    content_sha = hashlib.sha256(
        json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return (
        f"repository={args.get('owner')}/{args.get('repo')};tag={args.get('tag')};"
        f"commit_sha={args.get('expected_commit_sha')};content_sha256={content_sha}"
    )


@dataclass(frozen=True)
class GithubReleaseProjectEffect:
    checkpoint: ProjectCheckpoint
    effect: ProjectEffectRecord
    scope: str
    owner: str
    repo: str
    tag: str
    commit_sha: str
    title: str
    notes: str
    draft: bool
    prerelease: bool
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
            "repository_tag": self.tag,
            "repository_head_sha": self.commit_sha,
        }


def execute_github_release_project_effect(
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
    started = _begin_release_effect(
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
            tool_name=TOOL_GITHUB_CREATE_RELEASE,
        )
        return _finalize_release_result(task_manager, started, result=result, ctx=ctx)
    except ToolRuntimeError as exc:
        exc.details.update(
            _record_release_failure(task_manager, started, error=exc, ctx=ctx)
        )
        raise


def _begin_release_effect(
    *,
    task_manager: Any,
    task_id: str,
    actor_ref: str,
    args: Mapping[str, Any],
    ctx: Any,
) -> GithubReleaseProjectEffect:
    checkpoint = _require_project_checkpoint(task_manager, task_id)
    scope = github_release_action_scope(args)
    effect_id = (
        f"effect:github.create_release:{hashlib.sha256(scope.encode()).hexdigest()}"
    )
    preconditions = (
        f"github:repository:{args.get('owner')}/{args.get('repo')}",
        f"github:tag:{args.get('tag')}@{args.get('expected_commit_sha')}",
        f"github:release:{scope.rsplit('=', 1)[-1]}",
    )
    existing = load_project_effect_record(
        task_manager, task_id=task_id, effect_id=effect_id
    )
    replay = evaluate_project_effect_replay(
        existing, idempotency_key=scope, precondition_refs=preconditions
    )
    if replay.decision == ProjectEffectReplayDecision.BLOCK_STALE_PRECONDITION:
        raise ToolRuntimeError(
            "INVALID_REQUEST",
            "The release request changed since this action was recorded.",
            {"reason_code": replay.reason, "project_effect_id": effect_id},
        )
    preflight = read_release(args, ctx)
    data = _release_data(preflight, args)
    expected_sha = str(args.get("expected_commit_sha") or "")
    if str(data.get("tag_sha") or "") != expected_sha:
        raise ToolRuntimeError(
            "INVALID_REQUEST",
            "The existing tag does not dereference to the expected commit SHA.",
            {"reason_code": "github_release_tag_sha_mismatch"},
        )
    reconciled = False
    if existing is not None and replay.decision in {
        ProjectEffectReplayDecision.REUSE_EXISTING,
        ProjectEffectReplayDecision.BLOCK_DUPLICATE,
    }:
        was_started = existing.status == ProjectEffectStatus.STARTED
        effect = _resume_release_effect(
            task_manager,
            existing=existing,
            preflight=preflight,
            args=args,
            scope=scope,
            ctx=ctx,
        )
        reconciled = was_started and effect.status == ProjectEffectStatus.SUCCEEDED
    else:
        if data.get("release") is not None:
            raise ToolRuntimeError(
                "ALREADY_EXISTS",
                "A GitHub release already exists for this tag.",
                {"reason_code": "github_release_already_exists"},
            )
        permission = evaluate_project_permission(
            task_manager,
            task_id=task_id,
            tool_name=TOOL_GITHUB_CREATE_RELEASE,
            scope=scope,
        )
        if not permission.allowed:
            _raise_github_policy_denial(
                checkpoint=checkpoint,
                permission=permission,
                task_id=task_id,
                scope=scope,
                head_sha=expected_sha,
                ctx=ctx,
                tool_name=TOOL_GITHUB_CREATE_RELEASE,
            )
        effect = ProjectEffectRecord(
            effect_id=effect_id,
            task_id=task_id,
            idempotency_key=scope,
            actor_ref=actor_ref,
            capability_ref=TOOL_GITHUB_CREATE_RELEASE,
            precondition_refs=preconditions,
            approval_ref=permission.grant_id,
        )
        save_project_effect_record(task_manager, effect)
        consume_project_permission_grant(
            task_manager, task_id=task_id, grant_id=str(permission.grant_id or "")
        )
        ctx.github_create_release_preflight = preflight
    started = _release_state(checkpoint, effect, args, scope, reconciled=reconciled)
    emit_tool_invoke_operation_for_context(
        ctx=ctx,
        operation="invoke",
        tool_name=TOOL_GITHUB_CREATE_RELEASE,
        extra=started.facts(),
    )
    return started


def _release_state(
    checkpoint: ProjectCheckpoint,
    effect: ProjectEffectRecord,
    args: Mapping[str, Any],
    scope: str,
    *,
    reconciled: bool = False,
) -> GithubReleaseProjectEffect:
    return GithubReleaseProjectEffect(
        checkpoint=checkpoint,
        effect=effect,
        scope=scope,
        owner=str(args.get("owner") or ""),
        repo=str(args.get("repo") or ""),
        tag=str(args.get("tag") or ""),
        commit_sha=str(args.get("expected_commit_sha") or ""),
        title=str(args.get("title") or ""),
        notes=str(args.get("notes") or ""),
        draft=bool(args.get("draft")),
        prerelease=bool(args.get("prerelease")),
        reconciled=reconciled,
    )


def _resume_release_effect(
    task_manager: Any,
    *,
    existing: ProjectEffectRecord,
    preflight: Mapping[str, Any],
    args: Mapping[str, Any],
    scope: str,
    ctx: Any,
) -> ProjectEffectRecord:
    receipt = load_project_effect_receipt(
        task_manager, task_id=existing.task_id, effect_id=existing.effect_id
    )
    if existing.status == ProjectEffectStatus.STARTED:
        receipt = _release_receipt(preflight, args)
        if receipt["release_id"] is None:
            raise ToolRuntimeError(
                "UPSTREAM_ERROR",
                "The prior GitHub release request is unresolved; it was not repeated.",
                {
                    "reason_code": "github_release_readback_not_found",
                    "project_effect_uncertain": True,
                    "project_effect_id": existing.effect_id,
                    "repository_action_scope": scope,
                },
            )
        existing = existing.model_copy(
            update={
                "status": ProjectEffectStatus.SUCCEEDED,
                "result_ref": f"github:release:{receipt['release_id']}",
                "verification_refs": ("github.create_release:readback",),
                "non_reversible_reason": "OpenMinion does not delete a created release automatically.",
            }
        )
        save_project_effect_record(task_manager, existing, receipt=receipt)
    if receipt is None:
        raise ToolRuntimeError(
            "INTERNAL_ERROR",
            "The completed release effect has no receipt.",
            {
                "reason_code": "project_effect_receipt_missing",
                "project_effect_id": existing.effect_id,
            },
        )
    ctx.github_create_release_reconciled_result = _release_result_from_receipt(receipt)
    return existing


def _finalize_release_result(
    task_manager: Any,
    started: GithubReleaseProjectEffect,
    *,
    result: dict[str, Any],
    ctx: Any,
) -> dict[str, Any]:
    outputs = result.get("outputs")
    if result.get("status") == BRAIN_ACTION_STATUS_SUCCESS and isinstance(
        outputs, Mapping
    ):
        receipt = _release_receipt(
            outputs,
            {
                "owner": started.owner,
                "repo": started.repo,
                "tag": started.tag,
                "expected_commit_sha": started.commit_sha,
                "title": started.title,
                "notes": started.notes,
                "draft": started.draft,
                "prerelease": started.prerelease,
            },
        )
        if receipt["release_id"] is None:
            raise ToolRuntimeError(
                "INVALID_RESPONSE",
                "GitHub create-release result omitted release identity.",
                {"reason_code": "github_release_result_incomplete"},
            )
        effect = started.effect.model_copy(
            update={
                "status": ProjectEffectStatus.SUCCEEDED,
                "result_ref": f"github:release:{receipt['release_id']}",
                "non_reversible_reason": "OpenMinion does not delete a created release automatically.",
            }
        )
        save_project_effect_record(task_manager, effect, receipt=receipt)
        completed = GithubReleaseProjectEffect(**{**started.__dict__, "effect": effect})
        facts = completed.facts()
        emit_tool_invoke_operation_for_context(
            ctx=ctx,
            operation="completed",
            tool_name=TOOL_GITHUB_CREATE_RELEASE,
            extra=facts,
        )
        result["outputs"] = _with_project_facts(outputs, facts)
        return result
    failure = ToolRuntimeError(
        "UPSTREAM_ERROR", str(result.get("summary") or "GitHub release creation failed")
    )
    facts = _record_release_failure(task_manager, started, error=failure, ctx=ctx)
    raw_error = result.get("error")
    if isinstance(raw_error, dict):
        raw_error["code"] = "UPSTREAM_ERROR"
        raw_error["details"] = {**dict(raw_error.get("details") or {}), **facts}
    return result


def _record_release_failure(
    task_manager: Any,
    started: GithubReleaseProjectEffect,
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
    current = GithubReleaseProjectEffect(**{**started.__dict__, "effect": effect})
    facts = {**current.facts(), "project_effect_uncertain": uncertain}
    emit_tool_invoke_operation_for_context(
        ctx=ctx,
        operation="completed",
        tool_name=TOOL_GITHUB_CREATE_RELEASE,
        status="error",
        error_code="PROJECT_EFFECT_UNCERTAIN" if uncertain else "PROJECT_EFFECT_FAILED",
        extra=facts,
    )
    return facts


def _release_data(
    result: Mapping[str, Any], args: Mapping[str, Any]
) -> Mapping[str, Any]:
    data = result.get("data")
    if not isinstance(data, Mapping):
        raise ToolRuntimeError(
            "INVALID_RESPONSE",
            "GitHub release result omitted data.",
            {"reason_code": "github_release_result_invalid"},
        )
    expected = tuple(str(args.get(key) or "") for key in ("owner", "repo", "tag"))
    actual = tuple(str(data.get(key) or "") for key in ("owner", "repo", "tag"))
    if actual != expected:
        raise ToolRuntimeError(
            "INVALID_RESPONSE",
            "GitHub release result does not match the approved target.",
            {"reason_code": "github_release_result_mismatch"},
        )
    return data


def _release_receipt(
    result: Mapping[str, Any], args: Mapping[str, Any]
) -> dict[str, Any]:
    data = _release_data(result, args)
    source = result.get("source")
    release = data.get("release")
    if release is not None and not isinstance(release, Mapping):
        raise ToolRuntimeError(
            "INVALID_RESPONSE",
            "GitHub release result contains malformed release facts.",
            {"reason_code": "github_release_result_invalid"},
        )
    expected = {
        "owner": str(args.get("owner") or ""),
        "repo": str(args.get("repo") or ""),
        "tag": str(args.get("tag") or ""),
        "tag_sha": str(args.get("expected_commit_sha") or ""),
    }
    if str(data.get("tag_sha") or "") != expected["tag_sha"]:
        raise ToolRuntimeError(
            "INVALID_RESPONSE",
            "GitHub release result does not match the approved tag and commit.",
            {"reason_code": "github_release_result_mismatch"},
        )
    if release is not None and (
        str(release.get("tag") or "") != expected["tag"]
        or str(release.get("title") or "") != str(args.get("title") or "")
        or str(release.get("notes") or "") != str(args.get("notes") or "")
        or bool(release.get("draft")) != bool(args.get("draft"))
        or bool(release.get("prerelease")) != bool(args.get("prerelease"))
    ):
        raise ToolRuntimeError(
            "INVALID_RESPONSE",
            "GitHub release result does not match the approved release content.",
            {"reason_code": "github_release_result_mismatch"},
        )
    provider_id = (
        str(source.get("provider_id") or "") if isinstance(source, Mapping) else ""
    )
    if release is not None and not provider_id:
        raise ToolRuntimeError(
            "INVALID_RESPONSE",
            "GitHub release result omitted provider identity.",
            {"reason_code": "github_release_result_incomplete"},
        )
    release_data = dict(release or {})
    return {
        **expected,
        **release_data,
        "release_id": release_data.get("release_id"),
        "provider_id": provider_id,
    }


def _release_result_from_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
    release = {
        key: receipt.get(key)
        for key in (
            "release_id",
            "tag",
            "title",
            "notes",
            "draft",
            "prerelease",
            "html_url",
        )
    }
    return {
        "ok": True,
        "data": {
            "owner": receipt.get("owner"),
            "repo": receipt.get("repo"),
            "tag": receipt.get("tag"),
            "tag_sha": receipt.get("tag_sha"),
            "release": release,
            "reconciled": True,
        },
        "source": {"provider_id": str(receipt.get("provider_id") or "")},
    }


__all__ = ["execute_github_release_project_effect", "github_release_action_scope"]
