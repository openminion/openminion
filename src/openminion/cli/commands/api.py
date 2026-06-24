from __future__ import annotations

import argparse

from openminion.api.server import build_api_server
from openminion.cli.bootstrap.loader import load_config


def run_api(args) -> int:
    config = load_config(args.config)
    host = str(args.host or config.gateway.host)
    port = int(args.port or config.gateway.port)

    try:
        server = build_api_server(config_path=args.config, host=host, port=port)
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
