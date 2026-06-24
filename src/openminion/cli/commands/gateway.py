from __future__ import annotations

import argparse
import asyncio
from types import SimpleNamespace
from typing import Any

from openminion.base.logging import apply_logging_mode
from openminion.cli.parser.flags import add_json_output_flag
from openminion.cli.presentation.json_output import print_json_payload


def run_gateway(args: Any, app: Any) -> int:
    if bool(getattr(args, "quiet", False)):
        apply_logging_mode("interactive")

    if hasattr(app, "resolve_agent_profile") and hasattr(app, "resolve_gateway"):
        agent_profile = app.resolve_agent_profile(getattr(args, "agent_id", None))
        gateway = app.resolve_gateway(agent_profile.name)
    else:
        from openminion.base.config.core import resolve_default_agent_id as _rda

        try:
            _default_id = _rda(app.config)
            _default_profile = app.config.agents.get(_default_id)
        except Exception:
            _default_profile = None
        default_agent_name = str(getattr(_default_profile, "name", "openminion"))
        default_channel = str(getattr(_default_profile, "default_channel", "console"))
        agent_profile = SimpleNamespace(
            name=default_agent_name, default_channel=default_channel
        )
        gateway = app.gateway
    channel = (args.channel or agent_profile.default_channel).strip()
    if args.once:
        if not args.message:
            raise RuntimeError("--message is required with --once")

        result = asyncio.run(
            gateway.run_once(
                channel=channel,
                target=args.target,
                message=args.message,
                session_id=args.session_id,
                idempotency_key=args.idempotency_key,
                inbound_metadata={
                    "resume": str(bool(getattr(args, "resume", False))).lower(),
                    "reset_session": str(
                        bool(getattr(args, "reset_session", False))
                    ).lower(),
                },
            )
        )
        if args.json:
            print_json_payload(
                {
                    "id": result.id,
                    "channel": result.channel,
                    "target": result.target,
                    "body": result.body,
                    "metadata": {**result.metadata, "agent_id": agent_profile.name},
                }
            )
        return 0

    from openminion.cli.ux.verbosity import (
        resolve_progress,
        resolve_verbosity,
    )

    progress = resolve_progress(args, default="full")
    resolve_verbosity(args)
    asyncio.run(
        gateway.run_loop(
            channel=channel,
            target=args.target,
            show_progress=(progress == "full"),
        )
    )
    return 0


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    gateway = subparsers.add_parser("gateway", help="Gateway runtime controls")
    gateway_subcommands = gateway.add_subparsers(dest="gateway_command")
    gateway_run = gateway_subcommands.add_parser("run", help="Run gateway loop")
    gateway_run.add_argument(
        "--channel",
        default=None,
        help="Inbound channel name (default: selected agent default channel)",
    )
    gateway_run.add_argument("--target", default="local-user", help="Message target")
    gateway_run.add_argument(
        "--profile",
        "--agent-id",
        default=None,
        dest="agent_id",
        help="Configured profile id to run (compat: --agent-id)",
    )
    gateway_run.add_argument(
        "--override-provider",
        default=None,
        help="Run-scoped provider override applied after profile selection",
    )
    gateway_run.add_argument(
        "--override-model",
        default=None,
        help="Run-scoped model override applied after profile selection",
    )
    gateway_run.add_argument(
        "--override-system-prompt",
        default=None,
        help="Run-scoped system prompt override applied after profile selection",
    )
    gateway_run.add_argument(
        "--session-id",
        default=None,
        help="Optional explicit session id for continuity across runs",
    )
    gateway_run.add_argument(
        "--resume",
        action="store_true",
        help="Force reuse of the latest resolved thread even if settled",
    )
    gateway_run.add_argument(
        "--reset-session",
        action="store_true",
        help="Force creation of a fresh thread for this session",
    )
    gateway_run.add_argument("--once", action="store_true", help="Run a single turn")
    gateway_run.add_argument("--message", help="Input message for --once")
    gateway_run.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress INFO logs for cleaner terminal chat output",
    )
    gateway_run.add_argument(
        "--no-progress",
        action="store_true",
        help="Legacy alias for --progress off (CUC).",
    )
    gateway_run.add_argument(
        "--idempotency-key",
        default=None,
        help="Optional idempotency key for single-turn replay protection",
    )
    from openminion.cli.ux.verbosity import (
        add_progress_flag,
        add_verbosity_flag,
    )

    add_verbosity_flag(gateway_run)
    add_progress_flag(gateway_run, include_aliases=False)
    add_json_output_flag(gateway_run)
    gateway_run.set_defaults(handler=run_gateway, needs_app=True)
