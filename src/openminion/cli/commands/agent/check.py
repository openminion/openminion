from __future__ import annotations

import argparse
import asyncio
from time import perf_counter
from uuid import uuid4

from openminion.base.types import Message
from openminion.cli.commands.agent.runner import _resolve_agent_profile_and_service
from openminion.cli.parser.flags import add_json_output_flag
from openminion.cli.presentation.json_output import print_json_payload


def run_agent_check(args, app) -> int:
    agent_profile, agent_service = _resolve_agent_profile_and_service(args, app)
    channel = (args.channel or agent_profile.default_channel).strip()
    target = args.target
    message_text = args.message

    started = perf_counter()
    try:
        app.channels.get(channel)

        request_id = uuid4().hex
        inbound = Message(
            channel=channel,
            target=target,
            body=message_text,
            metadata={
                "session_id": f"runtime:agent-check:{request_id}",
                "request_id": request_id,
                "turn_id": request_id,
                "invocation_scope": "runtime",
                "diagnostic_scope": "runtime",
            },
        )
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
            "scope": "runtime",
            "request_id": request_id,
            "invocation_id": response.metadata.get("invocation_id", ""),
            "execution_id": response.metadata.get("execution_id", ""),
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
