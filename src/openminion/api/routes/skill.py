"""HTTP routes for skill catalog, proposal, and suggestion actions."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote

from openminion.api.operations import skill as operations
from openminion.modules.skill.constants import SKILL_STATUS_DEPRECATED

from .contracts import APIRouteContext, RouteResult


_SKILLS_LIST_RE = re.compile(r"/v1/skills")
_SKILL_DETAIL_RE = re.compile(r"/v1/skills/([^/]+)")
_SKILL_DISABLE_RE = re.compile(r"/v1/skills/([^/]+)/disable")
_PROPOSALS_LIST_RE = re.compile(r"/v1/skills/proposals")
_PROPOSAL_DETAIL_RE = re.compile(r"/v1/skills/proposals/([^/]+)")
_PROPOSAL_REVIEW_RE = re.compile(r"/v1/skills/proposals/([^/]+)/review")
_PROPOSAL_APPLY_RE = re.compile(r"/v1/skills/proposals/([^/]+)/apply")
_SUGGESTION_INBOX_RE = re.compile(r"/v1/skills/suggestions/inbox")
_SUGGESTION_STATUS_RE = re.compile(r"/v1/skills/suggestions/status")
_SUGGESTION_SURFACE_RE = re.compile(r"/v1/skills/suggestions/surface")


def handle_request(
    ctx: APIRouteContext,
    *,
    method_name: str,
    path: str,
    body: dict[str, Any] | None,
    query: str | None,
) -> RouteResult | None:
    if method_name == "GET" and _SUGGESTION_INBOX_RE.fullmatch(path):
        return operations.suggestion_inbox(ctx, query=query)
    if method_name == "GET" and _SUGGESTION_STATUS_RE.fullmatch(path):
        return operations.suggestion_status(ctx)
    if method_name == "POST" and _SUGGESTION_SURFACE_RE.fullmatch(path):
        return operations.suggestion_surface(ctx, body=body)

    match = _PROPOSAL_APPLY_RE.fullmatch(path)
    if method_name == "POST" and match:
        return operations.apply_proposal(ctx, proposal_id=unquote(match.group(1)))
    match = _PROPOSAL_REVIEW_RE.fullmatch(path)
    if method_name == "POST" and match:
        return operations.review_proposal(
            ctx,
            proposal_id=unquote(match.group(1)),
            body=body,
            path=path,
        )
    match = _PROPOSAL_DETAIL_RE.fullmatch(path)
    if method_name == "GET" and match:
        return operations.get_proposal(ctx, proposal_id=unquote(match.group(1)))
    if method_name == "GET" and _PROPOSALS_LIST_RE.fullmatch(path):
        return operations.list_proposals(ctx, query=query)

    match = _SKILL_DISABLE_RE.fullmatch(path)
    if method_name == "POST" and match:
        return operations.disable_skill(
            ctx,
            skill_id=unquote(match.group(1)),
            body=body,
            path=path,
        )
    match = _SKILL_DETAIL_RE.fullmatch(path)
    if method_name == "GET" and match:
        return operations.get_skill(ctx, skill_id=unquote(match.group(1)))
    if method_name == "GET" and _SKILLS_LIST_RE.fullmatch(path):
        return operations.list_skills(ctx, query=query)
    return None


__all__ = ["handle_request", "SKILL_STATUS_DEPRECATED"]
