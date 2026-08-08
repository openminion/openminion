from __future__ import annotations

import argparse
import asyncio
from time import perf_counter

from openminion.base.types import Message
from openminion.cli.parser.flags import add_json_output_flag
from openminion.cli.presentation.json_output import print_json_payload


def run_agent_check(args, app) -> int:
    if hasattr(app, "resolve_agent_profile") and hasattr(app, "resolve_agent_service"):
        agent_profile = app.resolve_agent_profile(getattr(args, "agent_id", None))
        agent_service = app.resolve_agent_service(agent_profile.name)
    else:
        requested_agent = str(getattr(args, "agent_id", "") or "").strip()
        from openminion.base.config.core import resolve_default_agent_id as _rda

        try:
            _default_id = _rda(app.config)
            _default_profile = app.config.agents.get(_default_id)
        except Exception:
            _default_profile = None
        default_agent_name = str(getattr(_default_profile, "name", "openminion"))
        default_channel = str(getattr(_default_profile, "default_channel", "console"))
        default_provider = str(getattr(app.provider, "name", "echo"))
        agent_profile = type(
            "_CompatAgentProfile",
            (),
            {
                "name": requested_agent or default_agent_name,
                "default_channel": default_channel,
                "provider": default_provider,
            },
        )()
        agent_service = app.agent
    channel = (args.channel or agent_profile.default_channel).strip()
    target = args.target
    message_text = args.message

    started = perf_counter()
    try:
        app.channels.get(channel)

        inbound = Message(channel=channel, target=target, body=message_text)
        response = asyncio.run(agent_service.run_turn(inbound))
        latency_ms = int((perf_counter() - started) * 1000)

        if args.deliver:
            outbound = Message(
                channel=response.channel,
                target=response.target,
                body=response.text,
                metadata=response.metadata,
            )
            app.channels.get(response.channel).send(outbound)

        payload = {
            "ok": True,
            "status": "healthy",
            "agent": agent_profile.name,
            "provider": response.metadata.get("provider", ""),
            "channel": response.channel,
            "target": response.target,
            "latency_ms": latency_ms,
            "response_chars": len(response.text),
            "metadata": response.metadata,
            "delivered": bool(args.deliver),
        }
        if args.json:
            print_json_payload(payload)
        else:
            print(
                "agent-check: OK "
                f"agent={payload['agent']} provider={payload['provider']} "
                f"channel={payload['channel']} latency_ms={payload['latency_ms']} "
                f"response_chars={payload['response_chars']}"
            )
        return 0
    except Exception as exc:
        payload = {
            "ok": False,
            "status": "unhealthy",
            "agent": agent_profile.name,
            "provider": agent_profile.provider,
            "channel": channel,
            "target": target,
            "error": str(exc),
        }
        if args.json:
            print_json_payload(payload)
        else:
            print(
                "agent-check: FAIL "
                f"agent={payload['agent']} provider={payload['provider']} "
                f"channel={payload['channel']} error={payload['error']}"
            )
        return 1


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    agent_check = subparsers.add_parser(
        "agent-check",
        help="Run a functional agent smoke check and return status metadata",
    )
    agent_check.add_argument(
        "--message",
        default="health check",
        help="Input message for smoke check (default: health check)",
    )
    agent_check.add_argument(
        "--target", default="doctor", help="Session or recipient target"
    )
    agent_check.add_argument(
        "--channel",
        default=None,
        help="Channel context (default: agent.default_channel from config)",
    )
    agent_check.add_argument(
        "--profile",
        "--agent-id",
        default=None,
        dest="agent_id",
        help="Configured profile id to run (compat: --agent-id)",
    )
    agent_check.add_argument(
        "--override-provider",
        default=None,
        help="Run-scoped provider override applied after profile selection",
    )
    agent_check.add_argument(
        "--override-model",
        default=None,
        help="Run-scoped model override applied after profile selection",
    )
    agent_check.add_argument(
        "--override-system-prompt",
        default=None,
        help="Run-scoped system prompt override applied after profile selection",
    )
    agent_check.add_argument(
        "--deliver", action="store_true", help="Deliver reply to channel backend"
    )
    add_json_output_flag(agent_check)
    agent_check.set_defaults(handler=run_agent_check, needs_app=True)
