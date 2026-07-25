from __future__ import annotations

import argparse
from collections.abc import Callable
from typing import Any

from openminion.cli.config import load_cli_config_from_args


def _resolve_run_api_dependencies() -> tuple[Callable[..., Any], Callable[..., Any]]:
    patched_load_config = globals().get("load_config")
    patched_build_api_server = globals().get("build_api_server")
    if patched_load_config is not None and patched_build_api_server is not None:
        return patched_load_config, patched_build_api_server

    from openminion.api.server import build_api_server

    return load_cli_config_from_args, build_api_server


def run_api(args: Any) -> int:
    load_config, build_api_server = _resolve_run_api_dependencies()
    config = _load_api_config(load_config, args)
    host = str(args.host or config.gateway.host)
    port = int(args.port or config.gateway.port)

    try:
        server = build_api_server(
            config_path=args.config,
            host=host,
            port=port,
            home_root=getattr(args, "home_root", None),
            data_root=getattr(args, "data_root", None),
        )
    except Exception as exc:
        print(f"API server failed to start on {host}:{port}: {exc}")
        return 1

    bound_host, bound_port = server.server_address
    print(f"API server listening on http://{bound_host}:{bound_port}")
    exit_code = 0
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("API server stopped")
    except Exception as exc:
        print(f"API server stopped unexpectedly: {exc}")
        exit_code = 1
    finally:
        try:
            server.server_close()
        except Exception as exc:
            print(f"API server shutdown failed: {exc}")
            exit_code = 1
    return exit_code


def _load_api_config(loader: Callable[..., Any], args: Any) -> Any:
    if loader is load_cli_config_from_args:
        return loader(args)
    return loader(args.config)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    api = subparsers.add_parser("api", help="HTTP API controls")
    api_subcommands = api.add_subparsers(dest="api_command")
    api_run = api_subcommands.add_parser("run", help="Run HTTP API server")
    api_run.add_argument(
        "--host", default=None, help="Bind host (default: config.gateway.host)"
    )
    api_run.add_argument(
        "--port",
        default=None,
        type=int,
        help="Bind port (default: config.gateway.port)",
    )
    api_run.set_defaults(handler=run_api, needs_app=False)
