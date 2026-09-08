from __future__ import annotations

from http import HTTPStatus
import re
from typing import Any, cast
from urllib.parse import parse_qsl, unquote

from openminion.api.server.client_artifacts import ClientArtifactError

from .contracts import APIRouteContext, RouteResult, error_route_result


_CATALOG_PATH = re.compile(r"/v1/client/sessions/([^/]+)/artifacts")
_ITEM_PATH = re.compile(r"/v1/client/sessions/([^/]+)/artifacts/([^/]+)")
_DECISION_PATH = re.compile(
    r"/v1/client/sessions/([^/]+)/artifacts/([^/]+)/(detach|restore)"
)


def handle_request(
    ctx: APIRouteContext,
    *,
    method_name: str,
    path: str,
    body: dict[str, Any] | None,
    query: str | None,
) -> RouteResult | None:
    if ctx.client_identity is None or ctx.client_artifacts is None:
        return None
    try:
        if method_name == "GET" and (match := _CATALOG_PATH.fullmatch(path)):
            params = _query(query, allowed={"cursor", "limit"})
            limit = _bounded_int(params.get("limit"), default=25, maximum=100)
            payload = ctx.client_artifacts.list_artifacts(
                ctx.client_identity,
                unquote(match.group(1)),
                cursor=params.get("cursor"),
                limit=limit,
            )
            return RouteResult(
                status=HTTPStatus.OK,
                payload={"ok": True, **payload},
                session_id=unquote(match.group(1)),
            )
        if method_name == "GET" and (match := _ITEM_PATH.fullmatch(path)):
            params = _query(query, allowed={"cursor", "limit_bytes"})
            limit_bytes = _bounded_int(
                params.get("limit_bytes"), default=64 * 1024, maximum=64 * 1024
            )
            session_id = unquote(match.group(1))
            content = ctx.client_artifacts.read_artifact(
                ctx.client_identity,
                session_id,
                unquote(match.group(2)),
                cursor=params.get("cursor"),
                limit_bytes=limit_bytes,
            )
            return RouteResult(
                status=HTTPStatus.OK,
                payload={"ok": True, "content": content},
                session_id=session_id,
            )
        if method_name == "POST" and (match := _DECISION_PATH.fullmatch(path)):
            if query or body != {"schema_version": 1}:
                raise _artifact_error("invalid_request")
            session_id = unquote(match.group(1))
            artifact = ctx.client_artifacts.decide(
                ctx.client_identity,
                session_id,
                unquote(match.group(2)),
                detached=match.group(3) == "detach",
                request_id=ctx.request_id,
            )
            return RouteResult(
                status=HTTPStatus.OK,
                payload={"ok": True, "artifact": artifact},
                session_id=session_id,
            )
    except ClientArtifactError as exc:
        return error_route_result(
            exc.status,
            code=exc.code,
            message=exc.message,
            details={},
            retryable=False,
        )
    return None


def _query(query: str | None, *, allowed: set[str]) -> dict[str, str]:
    pairs = parse_qsl(query or "", keep_blank_values=True)
    if any(key not in allowed for key, _ in pairs):
        raise _artifact_error("invalid_request")
    result: dict[str, str] = {}
    for key, value in pairs:
        if key in result or not value.strip():
            raise _artifact_error("invalid_request")
        result[key] = value.strip()
    return result


def _bounded_int(value: str | None, *, default: int, maximum: int) -> int:
    if value is None:
        return default
    try:
        resolved = int(value)
    except ValueError as exc:
        raise _artifact_error("invalid_request") from exc
    if resolved < 1 or resolved > maximum:
        raise _artifact_error("invalid_request")
    return resolved


def _artifact_error(code: str) -> Exception:
    return cast(
        Exception,
        ClientArtifactError(
            HTTPStatus.BAD_REQUEST,
            code,
            "Request is invalid.",
        ),
    )
