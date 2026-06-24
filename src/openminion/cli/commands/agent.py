from __future__ import annotations

import argparse
import asyncio

from openminion.base.config.core import resolve_default_agent_id
from openminion.base.types import Message
from openminion.cli.commands.agents import add_agent_operator_subcommands
from openminion.cli.parser.flags import add_json_output_flag
from openminion.cli.presentation.json_output import print_json_payload
from openminion.services.agent.memory.capsule import resolve_memory_root
from openminion.services.context.session import (
    SessionContextService,
    resolve_session_archive_root,
)
from openminion.modules.brain.paths import resolve_brain_sessions_db_path
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
from openminion.services.config import resolve_services_env
from openminion.services.constants import SERVICES_PROJECT_ID_ENV
from openminion.services.agent.memory.gateway_adapter import (
    DisabledMemoryGatewayAdapter,
    MemoryServiceGatewayAdapter,
)


_SESSION_CONTEXT_REQUIRED_METHODS = (
    "ensure_session_context",
    "count_messages",
    "list_messages_after_rowid",
    "list_recent_messages",
)


def _supports_session_context_store(sessions) -> bool:  # noqa: ANN001
    return all(
        callable(getattr(sessions, name, None))
        for name in _SESSION_CONTEXT_REQUIRED_METHODS
    )


def _resolve_agent_profile_and_service(args, app):
    if hasattr(app, "resolve_agent_profile") and hasattr(app, "resolve_agent_service"):
        agent_profile = app.resolve_agent_profile(getattr(args, "agent_id", None))
        agent_service = app.resolve_agent_service(agent_profile.name)
        return agent_profile, agent_service
    try:
        default_agent_id = resolve_default_agent_id(app.config)
        default_profile = app.config.agents[default_agent_id]
    except Exception:
        default_agent_id = "openminion"
        default_profile = None
    default_agent_name = str(getattr(default_profile, "name", "") or default_agent_id)
    default_channel = str(getattr(default_profile, "default_channel", "") or "console")
    agent_profile = type(
        "_CompatAgentProfile",
        (),
        {"name": default_agent_name, "default_channel": default_channel},
    )()
    agent_service = app.agent
    return agent_profile, agent_service


def _build_session_context_service(
    app, *, session_archive_root
) -> SessionContextService:
    runtime = app.config.runtime
    return SessionContextService(
        app.sessions,
        keep_recent_messages=max(
            1, int(getattr(runtime, "session_keep_recent_messages", 20))
        ),
        max_compact_per_turn=max(
            1,
            int(getattr(runtime, "session_max_compact_per_turn", 100)),
        ),
        summary_max_chars=max(
            256,
            int(getattr(runtime, "session_summary_max_chars", 8000)),
        ),
        archive_enabled=bool(getattr(runtime, "session_archive_enabled", True)),
        archive_root=session_archive_root,
        archive_ref_limit=max(
            1,
            int(getattr(runtime, "session_archive_ref_limit", 3)),
        ),
        token_budget=max(
            0,
            int(getattr(runtime, "session_context_token_budget", 0)),
        ),
        chars_per_token=max(
            0.1,
            float(getattr(runtime, "session_context_chars_per_token", 4.0)),
        ),
        summary_enrichment_enabled=bool(
            getattr(runtime, "session_summary_enrichment_enabled", False)
        ),
    )


def _build_memory_service(
    *,
    app,
    agent_profile,
    session_context: SessionContextService,
    memory_root,
):
    memory_enabled = bool(getattr(app.config.runtime, "memory_enabled", True))
    if not memory_enabled:
        return DisabledMemoryGatewayAdapter(agent_id=agent_profile.name)
    db_path = memory_root / "memory.db"
    store = SQLiteMemoryStore(db_path)
    svc = MemoryService(store=store)
    services_env = resolve_services_env(
        runtime_env=getattr(app.config.runtime, "env", {}),
    )
    return MemoryServiceGatewayAdapter(
        svc,
        agent_id=agent_profile.name,
        project_id=str(services_env.get(SERVICES_PROJECT_ID_ENV, "") or "").strip()
        or None,
        session_context=session_context,
        retrieval_max_chars=max(
            256,
            int(getattr(app.config.runtime, "memory_retrieval_max_chars", 2000)),
        ),
        brain_sessions_db_path=resolve_brain_sessions_db_path(
            storage_path=app.storage_path
        ),
    )


def _build_history_with_memory(
    *,
    app,
    session,
    session_context: SessionContextService,
    memory_service,
    channel: str,
    target,
    message: str,
) -> list[Message]:
    history: list[Message] = []
    if _supports_session_context_store(app.sessions):
        session_context.compact_session(session_id=session.id)
        history = session_context.build_history(
            session_id=session.id,
            channel=channel,
            target=target,
            recent_limit=max(
                1, int(getattr(app.config.runtime, "session_keep_recent_messages", 20))
            ),
        )
    memory_context = memory_service.build_context(
        session_id=session.id,
        user_message=message,
    )
    if memory_context:
        history.insert(
            0,
            Message(
                channel=channel,
                target=target,
                body=memory_context,
                metadata={
                    "role": "system",
                    "session_id": session.id,
                    "memory_scope": "agent_canonical",
                },
            ),
        )
    return history


def _render_agent_response(*, args, response, session_id: str, agent_name: str) -> None:
    if args.json:
        print_json_payload(
            {
                "text": response.text,
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
        print(response.text)


def run_agent(args, app) -> int:
    message = str(getattr(args, "message", "") or "").strip()
    if not message:
        raise RuntimeError(
            "`openminion agent` requires `--message` for a direct turn or an operator subcommand such as `ls` or `status`."
        )

    agent_profile, agent_service = _resolve_agent_profile_and_service(args, app)
    channel = (args.channel or agent_profile.default_channel).strip()
    target = args.target
    session = app.sessions.resolve_session(
        agent_id=agent_profile.name,
        channel=channel,
        target=target,
        session_id=args.session_id,
    )
    memory_root = resolve_memory_root(
        config=app.config,
        config_path=app.config_path,
        storage_path=app.storage_path,
    )
    session_archive_root = resolve_session_archive_root(
        config=app.config,
        config_path=app.config_path,
        storage_path=app.storage_path,
        memory_root=memory_root,
    )
    session_context = _build_session_context_service(
        app, session_archive_root=session_archive_root
    )
    memory_service = _build_memory_service(
        app=app,
        agent_profile=agent_profile,
        session_context=session_context,
        memory_root=memory_root,
    )
    history = _build_history_with_memory(
        app=app,
        session=session,
        session_context=session_context,
        memory_service=memory_service,
        channel=channel,
        target=target,
        message=message,
    )

    message_obj = Message(channel=channel, target=target, body=message)
    app.sessions.append_message(
        session_id=session.id,
        role="inbound",
        body=message_obj.body,
        metadata={"channel": channel, "target": target, "agent_id": agent_profile.name},
    )
    response = asyncio.run(agent_service.run_turn(message_obj, history=history))
    app.sessions.append_message(
        session_id=session.id,
        role="outbound",
        body=response.text,
        metadata={
            **response.metadata,
            "session_id": session.id,
            "agent_id": agent_profile.name,
        },
    )
    memory_service.record_turn(
        session_id=session.id,
        run_id=response.metadata.get("run_id", ""),
        request_id=response.metadata.get("request_id", ""),
        channel=channel,
        target=target,
        user_message=message,
        assistant_message=response.text,
    )

    if args.deliver:
        delivered = Message(
            channel=response.channel,
            target=response.target,
            body=response.text,
            metadata={
                **response.metadata,
                "session_id": session.id,
                "agent_id": agent_profile.name,
            },
        )
        app.channels.get(response.channel).send(delivered)

    _render_agent_response(
        args=args,
        response=response,
        session_id=session.id,
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
    from openminion.cli.ux.verbosity import (
        add_progress_flag,
        add_verbosity_flag,
    )

    add_verbosity_flag(agent)
    add_progress_flag(agent, include_aliases=True)
    add_json_output_flag(agent)
    add_agent_operator_subcommands(agent)
    agent.set_defaults(handler=run_agent, needs_app=True)
