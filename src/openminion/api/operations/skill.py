"""Skill catalog, proposal, and suggestion API operations."""

from __future__ import annotations

from collections.abc import Callable
from http import HTTPStatus
from typing import Any
from urllib.parse import parse_qs

from openminion.api.responses.serialization import error_response
from openminion.modules.skill.constants import SKILL_STATUS_DEPRECATED
from openminion.modules.skill.errors import SkillError
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
            filters["status"] = [
                item.strip() for item in str(status_raw).split(",") if item.strip()
            ]
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
    if body is None:
        return _error(
            HTTPStatus.BAD_REQUEST,
            code="invalid_request",
            message="JSON request body is required.",
            details={"path": path},
        )
    reason = str(body.get("reason", "") or "").strip()
    if not reason:
        return _error(
            HTTPStatus.BAD_REQUEST,
            code="invalid_request",
            message="`reason` is required to disable a skill.",
            details={"path": path},
        )

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
        updated = ctl.set_skill_status(
            skill_id=package.skill_id,
            new_status=SKILL_STATUS_DEPRECATED,
            promotion_path="api",
        )
        return RouteResult(
            status=HTTPStatus.OK,
            payload={
                "ok": True,
                "disabled": {
                    "skill_id": package.skill_id,
                    "previous_status": package.status,
                    "new_status": SKILL_STATUS_DEPRECATED,
                    "reason": reason,
                    "disabled_at": updated.updated_at,
                },
            },
        )

    return _with_skill(ctx, _build)


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
        from openminion.modules.skill.proposal.queue import (
            ProposalQueueError,
            list_proposals,
        )

        try:
            rows = list_proposals(
                ctl.store, queue_state=queue_state, limit=max(1, min(500, int(limit)))
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
    if body is None:
        return _error(
            HTTPStatus.BAD_REQUEST,
            code="invalid_request",
            message="JSON request body is required.",
            details={"path": path},
        )
    reviewer_id = str(body.get("reviewer_id", "") or "").strip()
    if not reviewer_id:
        return _error(
            HTTPStatus.BAD_REQUEST,
            code="invalid_request",
            message="`reviewer_id` is required to review a proposal.",
            details={"path": path},
        )
    review_policy_id = str(body.get("review_policy_id", "") or "").strip()
    criteria_raw = body.get("criterion_decisions") or []
    if not isinstance(criteria_raw, list) or not criteria_raw:
        return _error(
            HTTPStatus.BAD_REQUEST,
            code="invalid_request",
            message="`criterion_decisions` must be a non-empty list.",
            details={"path": path},
        )

    def _build(ctl: Skill) -> RouteResult:
        from openminion.modules.skill.proposal.queue import (
            ProposalQueueError,
            record_proposal_review,
        )

        try:
            review = record_proposal_review(
                ctl.store,
                proposal_id=proposal_id,
                reviewer_id=reviewer_id,
                review_policy_id=review_policy_id,
                criterion_decisions=criteria_raw,
            )
        except ProposalQueueError as exc:
            if "not found" in str(exc).lower():
                http_status = HTTPStatus.NOT_FOUND
                code = "NOT_FOUND"
            else:
                http_status = HTTPStatus.BAD_REQUEST
                code = "invalid_request"
            return _error(
                http_status,
                code=code,
                message=str(exc),
                details={"proposal_id": proposal_id},
            )
        except ValueError as exc:
            return _error(
                HTTPStatus.BAD_REQUEST,
                code="invalid_request",
                message=str(exc),
                details={"proposal_id": proposal_id},
            )
        return RouteResult(
            status=HTTPStatus.OK,
            payload={
                "ok": True,
                "proposal_id": proposal_id,
                "review": review.model_dump(mode="json"),
            },
        )

    return _with_skill(ctx, _build)


def apply_proposal(ctx: APIRouteContext, *, proposal_id: str) -> RouteResult:
    def _build(ctl: Skill) -> RouteResult:
        from openminion.modules.skill.proposal.queue import (
            ProposalQueueError,
            apply_proposal,
        )

        catalog_rows = ctl.list_skills({}) or []
        try:
            addition = apply_proposal(
                ctl.store,
                proposal_id=proposal_id,
                current_catalog=catalog_rows,
            )
        except ProposalQueueError as exc:
            if "not found" in str(exc).lower():
                http_status = HTTPStatus.NOT_FOUND
                code = "NOT_FOUND"
            else:
                http_status = HTTPStatus.BAD_REQUEST
                code = "invalid_request"
            return _error(
                http_status,
                code=code,
                message=str(exc),
                details={"proposal_id": proposal_id},
            )
        return RouteResult(
            status=HTTPStatus.OK,
            payload={
                "ok": True,
                "proposal_id": proposal_id,
                "addition": addition.model_dump(mode="json"),
            },
        )

    return _with_skill(ctx, _build)


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

        rows = list_active_suggestions(ctl.store, limit=max(1, min(500, int(limit))))
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
