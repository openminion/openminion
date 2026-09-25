"""Session-scoped custom command routes."""

import re
from http import HTTPStatus
from typing import Any
from urllib.parse import unquote

from openminion.api.queries.custom_commands import (
    CustomCommandQueryError,
    list_session_custom_commands,
    render_session_custom_command,
)

from .contracts import (
    APIRouteContext,
    RouteResult,
    exception_route_result,
    json_body_required_route_result,
)


_CUSTOM_COMMANDS_RE = re.compile(r"/v1/sessions/([^/]+)/custom-commands")
_CUSTOM_COMMAND_RENDER_RE = re.compile(
    r"/v1/sessions/([^/]+)/custom-commands/([^/]+)/render"
)


def handle_request(
    ctx: APIRouteContext,
    *,
    method_name: str,
    path: str,
    body: dict[str, Any] | None,
    query: str | None,
) -> RouteResult | None:
    del query
    if method_name == "GET" and (match := _CUSTOM_COMMANDS_RE.fullmatch(path)):
        session_id = unquote(match.group(1))
        try:
            payload = list_session_custom_commands(
                config_path=ctx.config_path,
                session_id=session_id,
                runtime=ctx.runtime,
            )
            return RouteResult(
                status=HTTPStatus.OK,
                payload={"ok": True, **payload},
                session_id=session_id,
            )
        except CustomCommandQueryError as exc:
            return _error_result(exc, session_id=session_id)
    if method_name != "POST" or not (
        match := _CUSTOM_COMMAND_RENDER_RE.fullmatch(path)
    ):
        return None
    session_id = unquote(match.group(1))
    if body is None:
        return json_body_required_route_result(path=path, session_id=session_id)
    arguments = body.get("arguments", "")
    if not isinstance(arguments, str):
        return _error_result(
            CustomCommandQueryError(
                "Custom command arguments must be a string.",
                "invalid_custom_command_arguments",
            ),
            session_id=session_id,
        )
    try:
        payload = render_session_custom_command(
            config_path=ctx.config_path,
            session_id=session_id,
            name=unquote(match.group(2)),
            arguments=arguments,
            runtime=ctx.runtime,
        )
        return RouteResult(
            status=HTTPStatus.OK,
            payload={"ok": True, **payload},
            session_id=session_id,
        )
    except CustomCommandQueryError as exc:
        return _error_result(exc, session_id=session_id)


def _error_result(exc: CustomCommandQueryError, *, session_id: str) -> RouteResult:
    return exception_route_result(
        HTTPStatus.NOT_FOUND
        if exc.code in {"session_not_found", "custom_command_not_found"}
        else HTTPStatus.BAD_REQUEST,
        code=exc.code,
        exc=exc,
        details={"session_id": session_id},
        retryable=False,
        session_id=session_id,
    )


__all__ = ["handle_request"]
