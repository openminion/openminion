from __future__ import annotations

import argparse
import asyncio
from types import SimpleNamespace

from openminion.base.config.core import resolve_default_agent_id
from openminion.cli.commands.agent.control import add_agent_operator_subcommands
from openminion.cli.parser.flags import add_json_output_flag
from openminion.cli.presentation.json_output import print_json_payload
from openminion.services.gateway.constants import (
    CALLER_HANDLES_DELIVERY_METADATA_KEY,
)


def _resolve_agent_profile_and_service(args, app):
    if hasattr(app, "resolve_agent_profile") and hasattr(app, "resolve_agent_service"):
        agent_profile = app.resolve_agent_profile(getattr(args, "agent_id", None))
        agent_service = app.resolve_agent_service(agent_profile.name)
        return agent_profile, agent_service
    requested_agent = str(getattr(args, "agent_id", "") or "").strip()
    try:
        default_agent_id = resolve_default_agent_id(app.config)
        default_profile = app.config.agents[default_agent_id]
    except Exception:
        default_agent_id = "openminion"
        default_profile = None
    default_agent_name = str(getattr(default_profile, "name", "") or default_agent_id)
    default_channel = str(getattr(default_profile, "default_channel", "") or "console")
    return (
        SimpleNamespace(
            name=requested_agent or default_agent_name,
            default_channel=default_channel,
            provider=str(getattr(app.provider, "name", "echo")),
        ),
        app.agent,
    )


def _render_agent_response(*, args, response, session_id: str, agent_name: str) -> None:
    text = str(getattr(response, "text", None) or getattr(response, "body", ""))
    if args.json:
        print_json_payload(
            {
                "text": text,
                "channel": response.channel,
                "target": response.target,
                "metadata": {
                    **response.metadata,
                    "session_id": session_id,
                    "agent_id": agent_name,
                },
            }
        )
    else:
        print(text)


def run_agent(args, app) -> int:
    message = str(getattr(args, "message", "") or "").strip()
    if not message:
        raise RuntimeError(
            "`openminion agent` requires `--message` for a direct turn or an operator subcommand such as `ls` or `status`."
        )

    agent_profile = (
        app.resolve_agent_profile(getattr(args, "agent_id", None))
        if hasattr(app, "resolve_agent_profile")
        else _resolve_agent_profile_and_service(args, app)[0]
    )
    gateway = (
        app.resolve_gateway(agent_profile.name)
        if hasattr(app, "resolve_gateway")
        else app.gateway
    )
    channel = (args.channel or agent_profile.default_channel).strip()
    target = args.target
    response = asyncio.run(
        gateway.run_once(
            channel=channel,
            target=target,
            message=message,
            session_id=args.session_id,
            deliver=bool(args.deliver),
            inbound_metadata={CALLER_HANDLES_DELIVERY_METADATA_KEY: "true"},
        )
    )
    session_id = str(response.metadata.get("session_id", ""))

    _render_agent_response(
        args=args,
        response=response,
        session_id=session_id,
        agent_name=agent_profile.name,
    )
    return 0


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    agent = subparsers.add_parser(
        "agent",
        help="Run an agent turn or manage agent runtimes",
    )
    agent.add_argument("--message", default="", help="Message body for a direct turn")
    agent.add_argument(
        "--target", default="local-user", help="Session or recipient target"
    )
    agent.add_argument(
        "--channel",
        default=None,
        help="Channel context (default: selected agent default channel)",
    )
    agent.add_argument(
        "--profile",
        "--agent-id",
        default=None,
        dest="agent_id",
        help="Configured profile id to run (compat: --agent-id)",
    )
    agent.add_argument(
        "--override-provider",
        default=None,
        help="Run-scoped provider override applied after profile selection",
    )
    agent.add_argument(
        "--override-model",
        default=None,
        help="Run-scoped model override applied after profile selection",
    )
    agent.add_argument(
        "--override-system-prompt",
        default=None,
        help="Run-scoped system prompt override applied after profile selection",
    )
    agent.add_argument(
        "--session-id",
        default=None,
        help="Optional explicit session id for continuity across runs",
    )
    agent.add_argument(
        "--deliver", action="store_true", help="Deliver reply to channel backend"
    )
    from openminion.cli.ux.verbosity import add_progress_flag, add_verbosity_flag

    add_verbosity_flag(agent)
    add_progress_flag(agent, include_aliases=True)
    add_json_output_flag(agent)
    add_agent_operator_subcommands(agent)
    agent.set_defaults(handler=run_agent, needs_app=True)
