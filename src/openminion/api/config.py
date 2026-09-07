from __future__ import annotations

import logging
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from os import PathLike
from pathlib import Path

from openminion.base.config import ConfigManager
from openminion.api.runtime import APIRuntime


@dataclass(frozen=True)
class APIRuntimeBootstrap:
    runtime: APIRuntime | None
    runtime_bootstrap_error: str | None
    ipc_token: str = ""


def _configured_ipc_token(
    config_path: str | None,
    *,
    home_root: str | PathLike[str] | None,
    data_root: str | PathLike[str] | None,
) -> str:
    try:
        manager = ConfigManager.load(
            config_path,
            home_root=Path(home_root).expanduser().resolve() if home_root else None,
            data_root=Path(data_root).expanduser().resolve() if data_root else None,
        )
    except (OSError, RuntimeError, TypeError, ValueError):
        return ""
    token = manager.base_config.runtime.ipc_token
    return token.strip() if isinstance(token, str) else ""


def _runtime_ipc_token(runtime: APIRuntime) -> str:
    token = getattr(getattr(runtime.config, "runtime", None), "ipc_token", "")
    return token.strip() if isinstance(token, str) else ""


def bootstrap_api_runtime(
    config_path: str | None,
    *,
    home_root: str | PathLike[str] | None = None,
    data_root: str | PathLike[str] | None = None,
) -> APIRuntimeBootstrap:
    try:
        runtime = APIRuntime.from_config_path(
            config_path,
            home_root=home_root,
            data_root=data_root,
        )
        return APIRuntimeBootstrap(
            runtime,
            None,
            _runtime_ipc_token(runtime),
        )
    except Exception as exc:  # noqa: BLE001
        runtime_bootstrap_error = str(exc)
        logging.getLogger("openminion.api").warning(
            "api runtime bootstrap failed; starting degraded mode error=%s",
            runtime_bootstrap_error,
        )
        return APIRuntimeBootstrap(
            None,
            runtime_bootstrap_error,
            _configured_ipc_token(
                config_path,
                home_root=home_root,
                data_root=data_root,
            ),
        )


def build_api_handler_class(
    base_handler: type[BaseHTTPRequestHandler],
    *,
    config_path: str | None,
    bootstrap: APIRuntimeBootstrap,
    class_name: str = "ConfiguredOpenMinionAPIHandler",
) -> type[BaseHTTPRequestHandler]:
    ipc_token = bootstrap.ipc_token
    if not ipc_token and bootstrap.runtime is not None:
        ipc_token = _runtime_ipc_token(bootstrap.runtime)
    return type(
        class_name,
        (base_handler,),
        {
            "config_path": config_path,
            "runtime": bootstrap.runtime,
            "runtime_bootstrap_error": bootstrap.runtime_bootstrap_error,
            "ipc_token": ipc_token,
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
