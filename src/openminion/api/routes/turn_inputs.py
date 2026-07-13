"""Turn-input queue routes."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote

from openminion.api.operations import turn_inputs as operations

from .contracts import APIRouteContext, RouteResult


_CANCEL_AND_RUN_NEXT_RE = re.compile(r"/v1/turn/([^/]+)/cancel-and-run-next")
_TURN_INPUTS_RE = re.compile(r"/v1/sessions/([^/]+)/turn-inputs/?")
_TURN_INPUT_RE = re.compile(r"/v1/sessions/([^/]+)/turn-inputs/([^/]+)/?")
_TURN_INPUT_MOVE_RE = re.compile(r"/v1/sessions/([^/]+)/turn-inputs/([^/]+)/move/?")


def handle_request(
    ctx: APIRouteContext,
    *,
    method_name: str,
    path: str,
    body: dict[str, Any] | None,
    query: str | None,
) -> RouteResult | None:
    match = _CANCEL_AND_RUN_NEXT_RE.fullmatch(path)
    if method_name == "POST" and match:
        return operations.cancel_and_run_next(
            ctx, path=path, trace_id=unquote(match.group(1)), body=body
        )

    match = _TURN_INPUTS_RE.fullmatch(path)
    if match:
        session_id = unquote(match.group(1))
        if method_name == "POST":
            return operations.enqueue_turn_input(
                ctx, path=path, session_id=session_id, body=body
            )
        if method_name == "GET":
            return operations.list_turn_inputs(
                ctx, path=path, session_id=session_id, query=query
            )

    match = _TURN_INPUT_MOVE_RE.fullmatch(path)
    if method_name == "POST" and match:
        return operations.move_turn_input(
            ctx,
            path=path,
            session_id=unquote(match.group(1)),
            queue_id=unquote(match.group(2)),
            body=body,
        )

    match = _TURN_INPUT_RE.fullmatch(path)
    if method_name == "DELETE" and match:
        return operations.drop_turn_input(
            ctx,
            path=path,
            session_id=unquote(match.group(1)),
            queue_id=unquote(match.group(2)),
            body=body,
        )
    return None


__all__ = ["handle_request"]
