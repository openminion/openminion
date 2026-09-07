"""Typed approval-resume route."""

from http import HTTPStatus
from typing import Any

from openminion.api.operations.approve_pending import process_approval_decision

from .contracts import APIRouteContext, RouteResult


_APPROVAL_RESUME_PATH = "/v1/approvals/resume"


def handle_request(
    ctx: APIRouteContext,
    *,
    method_name: str,
    path: str,
    body: dict[str, Any] | None,
    query: str | None,
) -> RouteResult | None:
    del query
    if method_name != "POST" or path != _APPROVAL_RESUME_PATH:
        return None
    payload = process_approval_decision(
        config_path=ctx.config_path,
        runtime=ctx.runtime,
        body=body or {},
    )
    return RouteResult(
        status=HTTPStatus.OK if payload.get("ok") else HTTPStatus.BAD_REQUEST,
        payload=payload,
    )


__all__ = ["handle_request"]
