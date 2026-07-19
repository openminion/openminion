from __future__ import annotations

import logging
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler

from openminion.api.runtime import APIRuntime


@dataclass(frozen=True)
class APIRuntimeBootstrap:
    runtime: APIRuntime | None
    runtime_bootstrap_error: str | None


def bootstrap_api_runtime(config_path: str | None) -> APIRuntimeBootstrap:
    try:
        return APIRuntimeBootstrap(APIRuntime.from_config_path(config_path), None)
    except Exception as exc:  # noqa: BLE001
        runtime_bootstrap_error = str(exc)
        logging.getLogger("openminion.api").warning(
            "api runtime bootstrap failed; starting degraded mode error=%s",
            runtime_bootstrap_error,
        )
        return APIRuntimeBootstrap(None, runtime_bootstrap_error)


def build_api_handler_class(
    base_handler: type[BaseHTTPRequestHandler],
    *,
    config_path: str | None,
    bootstrap: APIRuntimeBootstrap,
    class_name: str = "ConfiguredOpenMinionAPIHandler",
) -> type[BaseHTTPRequestHandler]:
    return type(
        class_name,
        (base_handler,),
        {
            "config_path": config_path,
            "runtime": bootstrap.runtime,
            "runtime_bootstrap_error": bootstrap.runtime_bootstrap_error,
        },
    )


def resolve_api_runtime(
    *,
    config_path: str | None,
    runtime: APIRuntime | None,
) -> tuple[APIRuntime, bool]:
    own_runtime = runtime is None
    active_runtime = runtime or APIRuntime.from_config_path(config_path)
    return active_runtime, own_runtime


def close_api_runtime_if_owned(
    runtime: APIRuntime | None,
    *,
    own_runtime: bool,
) -> None:
    if own_runtime and runtime is not None:
        runtime.close()


__all__ = [
    "APIRuntimeBootstrap",
    "bootstrap_api_runtime",
    "build_api_handler_class",
    "close_api_runtime_if_owned",
    "resolve_api_runtime",
]
