"""Memory record and provenance query adapters."""

from __future__ import annotations

from collections.abc import Mapping
from http import HTTPStatus
from typing import Any
from urllib.parse import parse_qs

from openminion.api.routes.contracts import (
    APIRouteContext,
    RouteResult,
    error_route_result,
    exception_route_result,
)
from openminion.modules.memory import (
    ListQueryOptions,
    MemoryNamespaceQueryInterface,
    SearchQueryOptions,
    default_provenance_recorder,
    resolve_namespace_filter,
)
from openminion.modules.memory.diagnostics.operability import serialize_for_json
from openminion.modules.memory.errors import (
    InvalidArgumentError,
    MemctlError,
    MemoryQueryUnavailableError,
)
from openminion.modules.memory.models import MemoryRecord
from sophiagraph.models import MemoryNamespace


_LIST_FIELDS = frozenset(
    {"namespace", "scope", "types", "tiers", "include_invalidated", "limit", "offset"}
)
_SEARCH_FIELDS = (_LIST_FIELDS - {"offset"}) | {"query"}


def _single_value(query: str | None, name: str) -> str | None:
    values = parse_qs(query or "", keep_blank_values=False).get(name)
    return values[0] if values else None


def _string_list(body: Mapping[str, Any], field: str) -> list[str] | None:
    value = body.get(field)
    if value is None:
        return None
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise InvalidArgumentError(f"{field} must be a list of non-empty strings")
    return [item.strip() for item in value]


def _integer(body: Mapping[str, Any], field: str, *, default: int, minimum: int) -> int:
    value = body.get(field, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise InvalidArgumentError(f"{field} must be an integer >= {minimum}")
    return int(value)


def _namespace(body: Mapping[str, Any]) -> tuple[MemoryNamespace, str | None]:
    namespace = body.get("namespace")
    if namespace is not None and not isinstance(namespace, Mapping):
        raise InvalidArgumentError("namespace must be an object")
    scope = body.get("scope")
    if scope is not None and (not isinstance(scope, str) or not scope.strip()):
        raise InvalidArgumentError("scope must be a non-empty string")
    return resolve_namespace_filter(scope=scope, namespace=namespace), scope


def _records(
    provider: MemoryNamespaceQueryInterface,
    body: Mapping[str, Any],
    *,
    search: bool,
) -> tuple[list[MemoryRecord], MemoryNamespace, str | None]:
    namespace, scope = _namespace(body)
    include_invalidated = body.get("include_invalidated", False)
    if not isinstance(include_invalidated, bool):
        raise InvalidArgumentError("include_invalidated must be a boolean")
    common = {
        "scopes": [scope.strip()] if scope else [],
        "types": _string_list(body, "types"),
        "tiers": _string_list(body, "tiers"),
        "include_invalidated": include_invalidated,
        "limit": _integer(body, "limit", default=20 if search else 100, minimum=1),
        "namespaces": [namespace],
    }
    if search:
        query = body.get("query")
        if not isinstance(query, str) or not query.strip():
            raise InvalidArgumentError("query must be a non-empty string")
        records = provider.search_records(
            SearchQueryOptions(query=query.strip(), **common)
        )
    else:
        records = provider.list_records(
            ListQueryOptions(
                offset=_integer(body, "offset", default=0, minimum=0), **common
            )
        )
    return list(records), namespace, scope


def record_query(
    ctx: APIRouteContext,
    *,
    path: str,
    body: dict[str, Any] | None,
    search: bool,
) -> RouteResult:
    if body is None:
        return _error(
            path,
            HTTPStatus.BAD_REQUEST,
            "invalid_request",
            "JSON request body is required.",
        )
    unknown = sorted(set(body) - (_SEARCH_FIELDS if search else _LIST_FIELDS))
    if unknown:
        return error_route_result(
            HTTPStatus.BAD_REQUEST,
            code="invalid_request",
            message=f"unknown request fields: {', '.join(unknown)}",
            details={"path": path, "fields": unknown},
            retryable=False,
        )
    provider = getattr(ctx.runtime, "memory_queries", None)
    if provider is None:
        message = (
            "memory query runtime is unavailable"
            if ctx.runtime is None
            else "memory query provider is unavailable"
        )
        return _error(
            path,
            HTTPStatus.SERVICE_UNAVAILABLE,
            "memory_unavailable",
            message,
            retryable=True,
        )
    try:
        records, namespace, scope = _records(provider, body, search=search)
    except InvalidArgumentError as exc:
        return _error(path, HTTPStatus.BAD_REQUEST, "invalid_request", str(exc))
    except MemoryQueryUnavailableError as exc:
        return _error(
            path,
            HTTPStatus.SERVICE_UNAVAILABLE,
            "memory_unavailable",
            str(exc),
            retryable=True,
        )
    except MemctlError as exc:
        return exception_route_result(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            code="memory_query_failed",
            exc=exc,
            details={"path": path},
            retryable=False,
        )
    return RouteResult(
        status=HTTPStatus.OK,
        payload={
            "count": len(records),
            "records": serialize_for_json(records),
            "namespace": namespace.as_dict(),
            "scope": scope.strip() if scope else None,
            "legacy_scope_only": body.get("namespace") is None,
        },
    )


def turn_trace(*, path: str, query: str | None) -> RouteResult:
    session_id = _single_value(query, "session_id")
    turn_id = _single_value(query, "turn_id")
    if not session_id:
        return _error(
            path,
            HTTPStatus.BAD_REQUEST,
            "invalid_request",
            "`session_id` query parameter is required.",
        )
    if not turn_id:
        return _error(
            path,
            HTTPStatus.BAD_REQUEST,
            "invalid_request",
            "`turn_id` query parameter is required.",
            session_id=session_id,
        )
    trace = default_provenance_recorder().get_turn_trace(
        session_id=session_id, turn_id=turn_id
    )
    if trace is None:
        return _error(
            path,
            HTTPStatus.NOT_FOUND,
            "not_found",
            f"no provenance trace recorded for session={session_id} turn={turn_id}",
            session_id=session_id,
        )
    return RouteResult(
        status=HTTPStatus.OK, payload=trace.to_dict(), session_id=session_id
    )


def traces_by_memory(*, path: str, query: str | None) -> RouteResult:
    memory_id = _single_value(query, "memory_id")
    if not memory_id:
        return _error(
            path,
            HTTPStatus.BAD_REQUEST,
            "invalid_request",
            "`memory_id` query parameter is required.",
        )
    traces = default_provenance_recorder().find_traces_citing_memory(memory_id)
    return RouteResult(
        status=HTTPStatus.OK,
        payload={
            "memory_id": memory_id,
            "trace_count": len(traces),
            "traces": [trace.to_dict() for trace in traces],
        },
    )


def _error(
    path: str,
    status: HTTPStatus,
    code: str,
    message: str,
    *,
    retryable: bool = False,
    session_id: str | None = None,
) -> RouteResult:
    return error_route_result(
        status,
        code=code,
        message=message,
        details={"path": path},
        retryable=retryable,
        session_id=session_id,
    )


__all__ = ["record_query", "traces_by_memory", "turn_trace"]
