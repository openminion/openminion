"""Skill catalog, proposal, and suggestion API operations."""

from collections.abc import Callable
from http import HTTPStatus
from typing import Any
from urllib.parse import parse_qs

from openminion.api.responses.serialization import error_response
from openminion.base.config.parse import split_comma_tokens
from openminion.modules.skill.errors import SkillError
from openminion.modules.skill.proposal.queue import (
    ProposalNotFoundError,
    ProposalQueueError,
)
from openminion.modules.skill.runtime.skill import Skill

from openminion.api.routes.contracts import APIRouteContext, RouteResult


def _error(
    status: HTTPStatus, *, code: str, message: str, details: dict[str, Any]
) -> RouteResult:
    resolved_status, payload = error_response(
        status,
        code=code,
        message=message,
        details=details,
        retryable=False,
    )
    return RouteResult(status=resolved_status, payload=payload)


def _operator_required() -> RouteResult:
    return _error(
        HTTPStatus.FORBIDDEN,
        code="SKILL_OPERATOR_AUTH_REQUIRED",
        message="Skill mutation requires an authenticated operator principal.",
        details={},
    )


def _with_skill(
    ctx: APIRouteContext, fn: Callable[[Skill], RouteResult]
) -> RouteResult:
    config_path = ctx.config_path
    try:
        ctl = Skill(config_path) if config_path else Skill()
    except SkillError as exc:
        return _error(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            code=exc.code,
            message=str(exc),
            details=exc.to_dict().get("details", {}),
        )
    except Exception as exc:
        return _error(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            code="skill_bootstrap_error",
            message=str(exc),
            details={"config_path": str(config_path or "")},
        )
    try:
        return fn(ctl)
    finally:
        ctl.close()


def _proposal_queue_error(exc: ProposalQueueError, *, proposal_id: str) -> RouteResult:
    message = str(exc)
    not_found = isinstance(exc, ProposalNotFoundError)
    return _error(
        HTTPStatus.NOT_FOUND if not_found else HTTPStatus.BAD_REQUEST,
        code="NOT_FOUND" if not_found else "invalid_request",
        message=message,
        details={"proposal_id": proposal_id},
    )


def list_skills(ctx: APIRouteContext, *, query: str | None) -> RouteResult:
    query_args = parse_qs(query or "", keep_blank_values=False)
    status_raw = query_args.get("status", [None])[0]
    scope = query_args.get("scope", [None])[0]
    agent_id = query_args.get("agent_id", [None])[0]
    tag = query_args.get("tag", [None])[0]
    tool = query_args.get("tool", [None])[0]

    def _build(ctl: Skill) -> RouteResult:
        filters: dict[str, Any] = {
            "scope": scope,
            "agent_id": agent_id,
            "tag": tag,
            "tool": tool,
        }
        if status_raw:
            filters["status"] = split_comma_tokens(status_raw)
        skills = ctl.list_skills(filters)
        return RouteResult(
            status=HTTPStatus.OK,
            payload={"ok": True, "skills": skills},
        )

    return _with_skill(ctx, _build)


def get_skill(ctx: APIRouteContext, *, skill_id: str) -> RouteResult:
    def _build(ctl: Skill) -> RouteResult:
        try:
            package = ctl.get_skill(skill_id, None)
        except SkillError as exc:
            return _error(
                HTTPStatus.NOT_FOUND
                if exc.code == "NOT_FOUND"
                else HTTPStatus.BAD_REQUEST,
                code=exc.code,
                message=str(exc),
                details=exc.to_dict().get("details", {}),
            )
        return RouteResult(
            status=HTTPStatus.OK,
            payload={"ok": True, "skill": package.to_dict()},
        )

    return _with_skill(ctx, _build)


def disable_skill(
    ctx: APIRouteContext, *, skill_id: str, body: dict[str, Any] | None, path: str
) -> RouteResult:
    return _operator_required()


def list_proposals(ctx: APIRouteContext, *, query: str | None) -> RouteResult:
    args = parse_qs(query or "", keep_blank_values=False)
    queue_state_raw = args.get("queue_state", [None])[0]
    limit_raw = args.get("limit", [None])[0]
    try:
        limit = int(limit_raw) if limit_raw else 50
    except (TypeError, ValueError):
        return _error(
            HTTPStatus.BAD_REQUEST,
            code="invalid_request",
            message="`limit` must be an integer.",
            details={"limit": limit_raw},
        )
    queue_state = (
        None if queue_state_raw in {None, "", "all"} else str(queue_state_raw).strip()
    )

    def _build(ctl: Skill) -> RouteResult:
        from openminion.modules.skill.proposal.queue import list_proposals

        try:
            rows = list_proposals(
                ctl.store, queue_state=queue_state, limit=max(1, min(500, limit))
            )
        except ProposalQueueError as exc:
            return _error(
                HTTPStatus.BAD_REQUEST,
                code="invalid_request",
                message=str(exc),
                details={"queue_state": queue_state_raw},
            )
        return RouteResult(
            status=HTTPStatus.OK,
            payload={"ok": True, "proposals": rows},
        )

    return _with_skill(ctx, _build)


def get_proposal(ctx: APIRouteContext, *, proposal_id: str) -> RouteResult:
    def _build(ctl: Skill) -> RouteResult:
        from openminion.modules.skill.proposal.queue import (
            ProposalQueueError,
            get_proposal,
        )

        try:
            record = get_proposal(ctl.store, proposal_id=proposal_id)
        except ProposalQueueError as exc:
            return _error(
                HTTPStatus.BAD_REQUEST,
                code="invalid_request",
                message=str(exc),
                details={"proposal_id": proposal_id},
            )
        if record is None:
            return _error(
                HTTPStatus.NOT_FOUND,
                code="NOT_FOUND",
                message="Proposal not found",
                details={"proposal_id": proposal_id},
            )
        return RouteResult(
            status=HTTPStatus.OK,
            payload={"ok": True, "proposal": record},
        )

    return _with_skill(ctx, _build)


def review_proposal(
    ctx: APIRouteContext,
    *,
    proposal_id: str,
    body: dict[str, Any] | None,
    path: str,
) -> RouteResult:
    return _operator_required()


def apply_proposal(ctx: APIRouteContext, *, proposal_id: str) -> RouteResult:
    return _operator_required()


def suggestion_inbox(ctx: APIRouteContext, *, query: str | None) -> RouteResult:
    args = parse_qs(query or "", keep_blank_values=False)
    limit_raw = args.get("limit", [None])[0]
    try:
        limit = int(limit_raw) if limit_raw else 50
    except (TypeError, ValueError):
        return _error(
            HTTPStatus.BAD_REQUEST,
            code="invalid_request",
            message="`limit` must be an integer.",
            details={"limit": limit_raw},
        )

    def _build(ctl: Skill) -> RouteResult:
        from openminion.modules.skill.suggestion import list_active_suggestions

        rows = list_active_suggestions(ctl.store, limit=max(1, min(500, limit)))
        return RouteResult(
            status=HTTPStatus.OK,
            payload={
                "ok": True,
                "suggestions": [row.to_dict() for row in rows],
            },
        )

    return _with_skill(ctx, _build)


def suggestion_status(ctx: APIRouteContext) -> RouteResult:
    def _build(ctl: Skill) -> RouteResult:
        from openminion.modules.skill.suggestion import suggestion_status

        status_payload = suggestion_status(ctl.store)
        return RouteResult(
            status=HTTPStatus.OK,
            payload={"ok": True, "status": status_payload.to_dict()},
        )

    return _with_skill(ctx, _build)


def suggestion_surface(
    ctx: APIRouteContext, *, body: dict[str, Any] | None
) -> RouteResult:
    body = body or {}
    try:
        batch_cap = body.get("batch_cap")
        if batch_cap is not None:
            batch_cap = max(1, min(50, int(batch_cap)))
        cooldown_seconds = body.get("cooldown_seconds")
        if cooldown_seconds is not None:
            cooldown_seconds = max(0, int(cooldown_seconds))
    except (TypeError, ValueError):
        return _error(
            HTTPStatus.BAD_REQUEST,
            code="invalid_request",
            message="`batch_cap` and `cooldown_seconds` must be integers.",
            details={},
        )

    def _build(ctl: Skill) -> RouteResult:
        from openminion.modules.skill.suggestion import (
            DEFAULT_SUGGESTION_BATCH_CAP,
            DEFAULT_SUGGESTION_COOLDOWN_SECONDS,
            run_suggestion_surface_pass,
        )

        report = run_suggestion_surface_pass(
            ctl.store,
            batch_cap=(
                batch_cap if batch_cap is not None else DEFAULT_SUGGESTION_BATCH_CAP
            ),
            cooldown_seconds=(
                cooldown_seconds
                if cooldown_seconds is not None
                else DEFAULT_SUGGESTION_COOLDOWN_SECONDS
            ),
        )
        return RouteResult(
            status=HTTPStatus.OK,
            payload={
                "ok": True,
                "surfaced": [row.to_dict() for row in report.surfaced],
                "auto_dismissed": list(report.auto_dismissed),
                "pending_remaining": int(report.pending_remaining),
            },
        )

    return _with_skill(ctx, _build)


__all__ = [
    "apply_proposal",
    "disable_skill",
    "get_proposal",
    "get_skill",
    "list_proposals",
    "list_skills",
    "review_proposal",
    "suggestion_inbox",
    "suggestion_status",
    "suggestion_surface",
]
