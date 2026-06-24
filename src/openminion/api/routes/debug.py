"""Debug route handlers for the developer API."""

from __future__ import annotations

import re
from http import HTTPStatus
from urllib.parse import unquote

from openminion.api.core.deps import is_debug_api_enabled, register_api_debug_providers
from openminion.api.responses.serialization import error_response
from openminion.services.diagnostics.debug import get_debug_registry

from .base import APIRouteContext, RouteResult


_DEBUG_MODULE_RE = re.compile(r"/v1/debug/modules/([^/]+)")


def _debug_disabled_response(path: str) -> RouteResult:
    status, payload = error_response(
        HTTPStatus.FORBIDDEN,
        code="debug_disabled",
        message="API debug endpoints are disabled by config.",
        details={
            "path": path,
            "required_flags": [
                "runtime.debug_enabled",
                "runtime.debug_api_enabled",
            ],
        },
        retryable=False,
    )
    return RouteResult(status=status, payload=payload)


def _build_debug_registry(ctx: APIRouteContext):
    registry = get_debug_registry()
    register_api_debug_providers(registry, ctx.runtime)
    return registry


def _handle_list_debug_modules(ctx: APIRouteContext, *, path: str) -> RouteResult:
    if not is_debug_api_enabled(config_path=ctx.config_path, runtime=ctx.runtime):
        return _debug_disabled_response(path)
    registry = _build_debug_registry(ctx)
    payload = {
        "ok": True,
        "modules": [p.to_dict() for p in registry.get_all_debug()],
    }
    return RouteResult(status=HTTPStatus.OK, payload=payload)


def _handle_debug_module(
    ctx: APIRouteContext,
    *,
    path: str,
    module_name: str,
) -> RouteResult:
    if not is_debug_api_enabled(config_path=ctx.config_path, runtime=ctx.runtime):
        return _debug_disabled_response(path)
    registry = _build_debug_registry(ctx)
    provider = registry.get_module(module_name)
    if provider is None:
        status, payload = error_response(
            HTTPStatus.NOT_FOUND,
            code="module_not_found",
            message=f"Unknown module: {module_name}",
            details={"module": module_name},
            retryable=False,
        )
        return RouteResult(status=status, payload=payload)
    try:
        payload = {"ok": True, "module": provider.get_debug().to_dict()}
    except Exception as exc:  # noqa: BLE001
        status, payload = error_response(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            code="debug_failed",
            message=str(exc),
            details={"module": module_name},
            retryable=False,
        )
        return RouteResult(status=status, payload=payload)
    return RouteResult(status=HTTPStatus.OK, payload=payload)


def handle_request(
    ctx: APIRouteContext,
    *,
    method_name: str,
    path: str,
    body: dict | None,
    query: str | None,
) -> RouteResult | None:
    del body, query
    if method_name == "GET" and path == "/v1/debug/modules":
        return _handle_list_debug_modules(ctx, path=path)

    if (
        method_name == "GET"
        and (debug_module_route := _DEBUG_MODULE_RE.fullmatch(path)) is not None
    ):
        module_name = unquote(debug_module_route.group(1))
        return _handle_debug_module(ctx, path=path, module_name=module_name)

    return None
