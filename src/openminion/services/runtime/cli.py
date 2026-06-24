import argparse
import json
import os
import sys
from typing import Any

from openminion.base.runtime.constants import DEFAULT_CONFIG_FILENAME


def _print_json(obj: Any) -> None:  # noqa: ANN401
    print(json.dumps(obj, indent=2))


def cmd_version(_args: argparse.Namespace) -> int:
    from . import __version__

    _print_json({"version": __version__, "module": "openminion-runtime"})
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    """Print effective config from the given YAML file (or defaults)."""
    from .settings import RuntimeConfig

    yaml_path = getattr(args, "yaml", None) or DEFAULT_CONFIG_FILENAME
    cfg = RuntimeConfig.from_yaml(yaml_path)
    _print_json(
        {
            "source": yaml_path if os.path.exists(yaml_path) else "<defaults>",
            "config": cfg.as_dict(),
        }
    )
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    """Validate the YAML config file and report any issues."""
    from .settings import RuntimeConfig

    yaml_path = getattr(args, "yaml", None) or DEFAULT_CONFIG_FILENAME

    issues: list[str] = []
    cfg: RuntimeConfig | None = None

    if not os.path.exists(yaml_path):
        print(f"WARNING: {yaml_path!r} not found — using defaults", file=sys.stderr)
    else:
        try:
            cfg = RuntimeConfig.from_yaml(yaml_path)
        except Exception as exc:
            issues.append(f"parse error: {exc}")

    if cfg is not None:
        if cfg.max_agents_hot < 1:
            issues.append("max_agents_hot must be >= 1")
        if cfg.max_global_concurrency < 1:
            issues.append("max_global_concurrency must be >= 1")
        if cfg.agent_ttl_seconds < 1:
            issues.append("agent_ttl_seconds must be >= 1")
        if cfg.sweep_interval_seconds < 1:
            issues.append("sweep_interval_seconds must be >= 1")

    if issues:
        _print_json({"valid": False, "issues": issues})
        return 1

    _print_json(
        {
            "valid": True,
            "config": (cfg.as_dict() if cfg else RuntimeConfig().as_dict()),
        }
    )
    return 0


def cmd_run_sample(args: argparse.Namespace) -> int:  # noqa: ARG001
    """Run a quick self-contained sample to verify the manager works."""
    from threading import Event
    from time import sleep

    from .manager import (
        AgentRuntimeManager,
        TurnRequest,
        TurnResponse,
    )

    print("Starting AgentRuntimeManager sample…", file=sys.stderr)

    results: list[str] = []

    def _executor(
        req: TurnRequest, emit_chunk: Any, cancel_event: Event
    ) -> TurnResponse:  # noqa: ANN401
        del emit_chunk, cancel_event
        sleep(0.05)
        return TurnResponse(final_text=f"echo:{req.input_text}")

    manager = AgentRuntimeManager(turn_executor=_executor)
    manager.start()
    try:
        handle = manager.submit_turn(
            TurnRequest(
                trace_id="cli-sample-1",
                agent_id="sample-agent",
                session_id="sample-session",
                input_text="hello",
            )
        )
        result = handle.result(timeout_s=5)
        results.append(result.final_text)
    finally:
        manager.shutdown()

    _print_json({"status": "ok", "results": results})
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m openminion.services.runtime",
        description="openminion-runtime operational CLI",
    )
    sub = p.add_subparsers(dest="command", metavar="COMMAND")

    sub.add_parser("version", help="Print package version as JSON")

    config_p = sub.add_parser("config", help="Print effective config as JSON")
    config_p.add_argument(
        "--yaml",
        metavar="PATH",
        default=DEFAULT_CONFIG_FILENAME,
        help=f"Path to runtime config (default: {DEFAULT_CONFIG_FILENAME})",
    )

    validate_p = sub.add_parser("validate", help="Validate config file")
    validate_p.add_argument(
        "--yaml",
        metavar="PATH",
        default=DEFAULT_CONFIG_FILENAME,
        help=f"Path to runtime config (default: {DEFAULT_CONFIG_FILENAME})",
    )

    sub.add_parser(
        "run-sample",
        help="Run a quick self-test to verify the manager works end-to-end",
    )

    return p


_COMMAND_MAP = {
    "version": cmd_version,
    "config": cmd_config,
    "validate": cmd_validate,
    "run-sample": cmd_run_sample,
}


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    handler = _COMMAND_MAP.get(args.command)
    if handler is None:
        print(f"Unknown command: {args.command}", file=sys.stderr)
        return 1

    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
