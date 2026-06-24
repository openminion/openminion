from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, TypeVar

from openminion.cli.config import resolve_cli_roots

from openminion.cli.presentation import styles
from openminion.cli.presentation.json_output import print_json_payload
from openminion.services.stats import StatsService, format_session_stats_summary

from ..runtime import ChatRuntimeState
from ..session import with_session_store
from ..ui import (
    clear_screen,
    handle_theme,
    print_chat_help,
    print_grouped_menu,
    print_status,
)


@dataclass
class ChatCommandResult:
    handled: bool
    exit: bool = False
    agent_id: str | None = None
    session_id: str | None = None
    new_conversation: bool = False
    new_session: bool = False
    rotate_session_on_agent_change: bool = False


@dataclass
class ChatCommandHandlers:
    print_tools: Callable[..., None]
    handle_debug_command: Callable[..., None]
    handle_pair_status: Callable[..., None]
    handle_pair_create: Callable[..., None]
    handle_pair_revoke: Callable[..., None]
    handle_trust_command: Callable[..., None]
    handle_untrust_command: Callable[..., None]
    handle_grants_command: Callable[..., None]
    handle_policy_command: Callable[..., None]
    handle_skill_command: Callable[..., None]
    handle_identity_command: Callable[..., None]
    handle_sidecar_command: Callable[..., None]


_T = TypeVar("_T")


def _with_session_store(
    *,
    args,
    runtime_state: ChatRuntimeState,
    default: _T,
    operation: Callable[[Any], _T],
) -> _T:
    return with_session_store(
        config_path=getattr(args, "config", None),
        runtime_state=runtime_state,
        default=default,
        operation=operation,
    )


def _parse_role_option(tokens: list[str]) -> tuple[str, list[str]]:
    if "--role" not in tokens:
        return "participant", tokens
    index = tokens.index("--role")
    if index + 1 >= len(tokens):
        raise ValueError("missing value for --role")
    role = str(tokens[index + 1]).strip().lower() or "participant"
    remaining = tokens[:index] + tokens[index + 2 :]
    return role, remaining


def _print_session_stats_summary(store: Any, *, session_id: str) -> None:
    summary = StatsService(store).get_session_stats(session_id)
    print(format_session_stats_summary(summary))


def _print_participants(
    participants: list[Any], *, active_agent_id: str | None
) -> None:
    if not participants:
        print("No participants.")
        return
    print("TYPE   ID               ROLE         CHANNEL    JOINED    ACTIVE")
    print("-----  ---------------  -----------  ---------  --------  ------")
    for participant in participants:
        participant_type = str(getattr(participant, "participant_type", "") or "")
        participant_id = str(getattr(participant, "participant_id", "") or "")
        role = str(getattr(participant, "role", "") or "")
        channel = str(getattr(participant, "channel", "") or "")
        joined_at = str(getattr(participant, "joined_at", "") or "")[:10]
        is_active = (
            participant_type == "agent"
            and participant_id
            and participant_id == str(active_agent_id or "").strip()
        )
        print(
            f"{participant_type:<5}  {participant_id:<15}  {role:<11}  "
            f"{channel:<9}  {joined_at:<8}  {('yes' if is_active else ''):<6}"
        )


def check_search_available(config) -> tuple[bool, str]:
    from .context import _get_search_provider_info

    config_env = getattr(getattr(config, "runtime", object()), "env", {})
    info = _get_search_provider_info(env=config_env)

    if not info.get("available_providers"):
        return False, "no search provider configured (missing API key)"

    resolved = ",".join(str(item) for item in info.get("available_providers", []))
    return True, f"providers={resolved}"


def _handle_simple_status_command(
    *,
    line: str,
    args,
    config,
    agent_id: str,
    session_id: str,
    transport: str,
    mode: str,
    runtime_state: ChatRuntimeState,
) -> ChatCommandResult | None:
    if line in {"/", "/help", "/?"}:
        print_chat_help()
        return ChatCommandResult(handled=True)
    if line == "/status":
        print_status(
            agent_id=agent_id,
            session_id=session_id,
            transport=transport,
            mode=mode,
            config=config,
        )
        return ChatCommandResult(handled=True)
    if line == "/stats":
        rendered = _with_session_store(
            args=args,
            runtime_state=runtime_state,
            default=False,
            operation=lambda store: (
                _print_session_stats_summary(store, session_id=session_id),
                True,
            )[1],
        )
        if not rendered:
            print(styles.style(styles.StyleToken.ERROR, "stats unavailable"))
        return ChatCommandResult(handled=True)
    if line == "/clear":
        clear_screen()
        return ChatCommandResult(handled=True)
    if line == "/exit":
        return ChatCommandResult(handled=True, exit=True)
    return None


def _handle_plan_or_goal_command(
    *, line: str, args, session_id: str
) -> ChatCommandResult | None:
    if line.startswith("/plan"):
        from .plan import handle_plan_command

        handle_plan_command(line, session_id=session_id)
        return ChatCommandResult(handled=True)
    if line.startswith("/goal"):
        from .goal import handle_goal_command

        handle_goal_command(
            line,
            session_id=session_id,
            config_path=getattr(args, "config", None),
        )
        return ChatCommandResult(handled=True)
    return None


def _handle_agent_command(
    *, line: str, agent_id: str, config
) -> ChatCommandResult | None:
    if line == "/agent":
        print(styles.style(styles.StyleToken.ERROR, "usage: /agent <id>"))
        return ChatCommandResult(handled=True)
    if line in {"/agent inspect", "/inspect"}:
        from .session import _handle_agent_inspect

        _handle_agent_inspect(agent_id=agent_id, config=config)
        return ChatCommandResult(handled=True)
    if line.startswith("/agent "):
        next_agent = line.split(" ", 1)[1].strip()
        if next_agent:
            if next_agent == agent_id:
                print(styles.style(styles.StyleToken.SUCCESS, f"agent={next_agent}"))
                return ChatCommandResult(handled=True, agent_id=next_agent)
            return ChatCommandResult(
                handled=True,
                agent_id=next_agent,
                rotate_session_on_agent_change=True,
            )
        return ChatCommandResult(handled=True)
    return None


def _handle_session_command(*, line: str, args) -> ChatCommandResult | None:
    if line == "/session":
        print(styles.style(styles.StyleToken.ERROR, "usage: /session <id>"))
        return ChatCommandResult(handled=True)
    if line.startswith("/session "):
        next_session = line.split(" ", 1)[1].strip()
        if next_session:
            print(styles.style(styles.StyleToken.SUCCESS, f"session={next_session}"))
            return ChatCommandResult(handled=True, session_id=next_session)
        return ChatCommandResult(handled=True)
    if line == "/new":
        return ChatCommandResult(handled=True, new_conversation=True)
    if line == "/new session":
        return ChatCommandResult(handled=True, new_session=True)
    if line == "/sessions":
        from types import SimpleNamespace

        from openminion.cli.commands.sessions import run_sessions_list

        run_sessions_list(
            SimpleNamespace(
                agent=None,
                status=None,
                channel=None,
                limit=10,
                output_json=False,
                config=getattr(args, "config", None),
                home_root=getattr(args, "home_root", None),
                data_root=getattr(args, "data_root", None),
            )
        )
        return ChatCommandResult(handled=True)
    return None


def _handle_invite_command(
    *,
    line: str,
    args,
    session_id: str,
    transport: str,
    runtime_state: ChatRuntimeState,
) -> ChatCommandResult | None:
    if line == "/invite":
        print(
            styles.style(
                styles.StyleToken.ERROR, "usage: /invite <agent-id> [--role <role>]"
            )
        )
        return ChatCommandResult(handled=True)
    if not line.startswith("/invite "):
        return None
    raw_tokens = [token for token in line.split()[1:] if token.strip()]
    try:
        role, tokens = _parse_role_option(raw_tokens)
    except ValueError as exc:
        print(styles.style(styles.StyleToken.ERROR, f"usage error: {exc}"))
        return ChatCommandResult(handled=True)
    if len(tokens) != 1:
        print(
            styles.style(
                styles.StyleToken.ERROR, "usage: /invite <agent-id> [--role <role>]"
            )
        )
        return ChatCommandResult(handled=True)
    invited_agent = tokens[0].strip()
    participant_count = _with_session_store(
        args=args,
        runtime_state=runtime_state,
        default=0,
        operation=lambda store: (
            store.add_participant(
                session_id=session_id,
                participant_type="agent",
                participant_id=invited_agent,
                channel=transport,
                role=role,
                display_name=invited_agent,
            ),
            len(store.list_participants(session_id)),
        )[1],
    )
    print(
        styles.style(
            styles.StyleToken.SUCCESS,
            f"invited agent={invited_agent} participants={participant_count}",
        )
    )
    return ChatCommandResult(handled=True)


def _handle_activate_command(
    *, line: str, args, session_id: str, runtime_state: ChatRuntimeState
) -> ChatCommandResult | None:
    if line == "/activate":
        print(styles.style(styles.StyleToken.ERROR, "usage: /activate <agent-id>"))
        return ChatCommandResult(handled=True)
    if not line.startswith("/activate "):
        return None
    next_agent = line.split(" ", 1)[1].strip()
    if not next_agent:
        print(styles.style(styles.StyleToken.ERROR, "usage: /activate <agent-id>"))
        return ChatCommandResult(handled=True)
    error = _with_session_store(
        args=args,
        runtime_state=runtime_state,
        default="room store unavailable",
        operation=lambda store: (
            store.set_active_agent(session_id=session_id, agent_id=next_agent),
            "",
        )[1],
    )
    if error:
        print(styles.style(styles.StyleToken.ERROR, error))
        return ChatCommandResult(handled=True)
    print(styles.style(styles.StyleToken.SUCCESS, f"active_agent={next_agent}"))
    return ChatCommandResult(handled=True, agent_id=next_agent)


def _handle_kick_command(
    *, line: str, args, session_id: str, runtime_state: ChatRuntimeState
) -> ChatCommandResult | None:
    if line == "/kick":
        print(
            styles.style(
                styles.StyleToken.ERROR,
                "usage: /kick <type>:<id> or /kick <id>",
            )
        )
        return ChatCommandResult(handled=True)
    if not line.startswith("/kick "):
        return None
    target_spec = line.split(" ", 1)[1].strip()
    if not target_spec:
        print(
            styles.style(
                styles.StyleToken.ERROR,
                "usage: /kick <type>:<id> or /kick <id>",
            )
        )
        return ChatCommandResult(handled=True)

    def _remove(store: Any) -> tuple[bool, str]:
        participants = store.list_participants(session_id)
        participant_type = ""
        participant_id = ""
        if ":" in target_spec:
            participant_type, participant_id = target_spec.split(":", 1)
        else:
            matches = [
                item
                for item in participants
                if str(getattr(item, "participant_id", "")).strip() == target_spec
            ]
            if len(matches) == 1:
                participant_type = str(matches[0].participant_type)
                participant_id = str(matches[0].participant_id)
            else:
                participant_type = "agent"
                participant_id = target_spec
        removed = store.remove_participant(
            session_id=session_id,
            participant_type=participant_type,
            participant_id=participant_id,
        )
        return removed, f"{participant_type}:{participant_id}"

    removed, removed_label = _with_session_store(
        args=args,
        runtime_state=runtime_state,
        default=(False, target_spec),
        operation=_remove,
    )
    if not removed:
        print(
            styles.style(
                styles.StyleToken.ERROR, f"participant not found: {removed_label}"
            )
        )
        return ChatCommandResult(handled=True)
    print(styles.style(styles.StyleToken.SUCCESS, f"removed {removed_label}"))
    return ChatCommandResult(handled=True)


def _handle_join_command(
    *,
    line: str,
    args,
    session_id: str,
    transport: str,
    runtime_state: ChatRuntimeState,
) -> ChatCommandResult | None:
    if line == "/join":
        print(
            styles.style(
                styles.StyleToken.ERROR, "usage: /join <human-id> [--role <role>]"
            )
        )
        return ChatCommandResult(handled=True)
    if not line.startswith("/join "):
        return None
    raw_tokens = [token for token in line.split()[1:] if token.strip()]
    try:
        role, tokens = _parse_role_option(raw_tokens)
    except ValueError as exc:
        print(styles.style(styles.StyleToken.ERROR, f"usage error: {exc}"))
        return ChatCommandResult(handled=True)
    if len(tokens) != 1:
        print(
            styles.style(
                styles.StyleToken.ERROR, "usage: /join <human-id> [--role <role>]"
            )
        )
        return ChatCommandResult(handled=True)
    human_id = tokens[0].strip()

    def _join(store: Any) -> int:
        store.add_participant(
            session_id=session_id,
            participant_type="human",
            participant_id=human_id,
            channel=transport,
            role=role,
            display_name=human_id,
        )
        store.update_session_metadata(
            session_id=session_id,
            patch={"local_human_id": human_id},
        )
        return len(store.list_participants(session_id))

    participant_count = _with_session_store(
        args=args,
        runtime_state=runtime_state,
        default=0,
        operation=_join,
    )
    print(
        styles.style(
            styles.StyleToken.SUCCESS,
            f"joined human={human_id} role={role} participants={participant_count}",
        )
    )
    return ChatCommandResult(handled=True)


def _handle_participants_listing(
    *, line: str, args, session_id: str, runtime_state: ChatRuntimeState
) -> ChatCommandResult | None:
    if line != "/participants":
        return None
    payload = _with_session_store(
        args=args,
        runtime_state=runtime_state,
        default=([], None),
        operation=lambda store: (
            store.list_participants(session_id),
            store.get_active_agent(session_id),
        ),
    )
    participants, active_agent_id = payload
    _print_participants(participants, active_agent_id=active_agent_id)
    return ChatCommandResult(handled=True)


def _handle_participant_command(
    *,
    line: str,
    args,
    session_id: str,
    transport: str,
    runtime_state: ChatRuntimeState,
) -> ChatCommandResult | None:
    for handler in (
        lambda: _handle_invite_command(
            line=line,
            args=args,
            session_id=session_id,
            transport=transport,
            runtime_state=runtime_state,
        ),
        lambda: _handle_activate_command(
            line=line, args=args, session_id=session_id, runtime_state=runtime_state
        ),
        lambda: _handle_participants_listing(
            line=line, args=args, session_id=session_id, runtime_state=runtime_state
        ),
        lambda: _handle_kick_command(
            line=line, args=args, session_id=session_id, runtime_state=runtime_state
        ),
        lambda: _handle_join_command(
            line=line,
            args=args,
            session_id=session_id,
            transport=transport,
            runtime_state=runtime_state,
        ),
    ):
        result = handler()
        if result is not None:
            return result
    return None


def _handle_handler_dispatched_command(
    *,
    line: str,
    args,
    config,
    agent_id: str,
    session_id: str,
    transport: str,
    runtime_state: ChatRuntimeState,
    last_artifacts: list[dict],
    last_turn_debug: dict[str, Any],
    handlers: ChatCommandHandlers,
) -> ChatCommandResult | None:
    if line == "/tools":
        handlers.print_tools(args, runtime_state, quiet=runtime_state.quiet)
        return ChatCommandResult(handled=True)
    if line == "/artifacts":
        print_json_payload(last_artifacts)
        return ChatCommandResult(handled=True)
    if line.startswith("/debug"):
        handlers.handle_debug_command(
            line=line,
            config=config,
            agent_id=agent_id,
            session_id=session_id,
            transport=transport,
            last_turn_debug=last_turn_debug,
            endpoint=runtime_state.endpoint,
        )
        return ChatCommandResult(handled=True)
    if line in {"/menu", "/help", "/?"}:
        print_grouped_menu(config=config)
        return ChatCommandResult(handled=True)
    if line == "/theme" or line.startswith("/theme "):
        data_root = getattr(runtime_state, "data_root", None)
        if data_root is None:
            try:
                roots = resolve_cli_roots(args)
                data_root = getattr(roots, "data_root", None)
            except (AttributeError, TypeError, ValueError):
                data_root = None
        handle_theme(line=line, data_root=data_root)
        return ChatCommandResult(handled=True)
    if line in {"/pair", "/pair status"}:
        handlers.handle_pair_status(config=config)
        return ChatCommandResult(handled=True)
    if line.startswith("/pair create"):
        handlers.handle_pair_create(line=line, config=config)
        return ChatCommandResult(handled=True)
    if line.startswith("/pair revoke"):
        handlers.handle_pair_revoke(line=line, config=config)
        return ChatCommandResult(handled=True)
    if line.startswith("/trust"):
        handlers.handle_trust_command(line=line, config=config, session_id=session_id)
        return ChatCommandResult(handled=True)
    if line.startswith("/untrust"):
        handlers.handle_untrust_command(line=line, config=config, session_id=session_id)
        return ChatCommandResult(handled=True)
    if line == "/grants":
        handlers.handle_grants_command(config=config, session_id=session_id)
        return ChatCommandResult(handled=True)
    if line.startswith("/policy"):
        handlers.handle_policy_command(
            line=line,
            config=config,
            agent_id=agent_id,
            session_id=session_id,
        )
        return ChatCommandResult(handled=True)
    if line.startswith("/skill"):
        handlers.handle_skill_command(
            line=line,
            config=config,
            agent_id=agent_id,
            session_id=session_id,
        )
        return ChatCommandResult(handled=True)
    if line.startswith("/identity"):
        handlers.handle_identity_command(line=line, config=config, agent_id=agent_id)
        return ChatCommandResult(handled=True)
    if line.startswith("/sidecar"):
        handlers.handle_sidecar_command(line=line, config=config, args=args)
        return ChatCommandResult(handled=True)
    return None


def handle_chat_command(
    *,
    line: str,
    args,
    config,
    agent_id: str,
    session_id: str,
    transport: str,
    mode: str,
    runtime_state: ChatRuntimeState,
    last_artifacts: list[dict],
    last_turn_debug: dict[str, Any],
    handlers: ChatCommandHandlers,
) -> ChatCommandResult:
    result = _handle_simple_status_command(
        line=line,
        args=args,
        config=config,
        agent_id=agent_id,
        session_id=session_id,
        transport=transport,
        mode=mode,
        runtime_state=runtime_state,
    )
    if result is not None:
        return result
    result = _handle_plan_or_goal_command(line=line, args=args, session_id=session_id)
    if result is not None:
        return result
    result = _handle_agent_command(line=line, agent_id=agent_id, config=config)
    if result is not None:
        return result
    result = _handle_participant_command(
        line=line,
        args=args,
        session_id=session_id,
        transport=transport,
        runtime_state=runtime_state,
    )
    if result is not None:
        return result
    result = _handle_session_command(line=line, args=args)
    if result is not None:
        return result
    result = _handle_handler_dispatched_command(
        line=line,
        args=args,
        config=config,
        agent_id=agent_id,
        session_id=session_id,
        transport=transport,
        runtime_state=runtime_state,
        last_artifacts=last_artifacts,
        last_turn_debug=last_turn_debug,
        handlers=handlers,
    )
    if result is not None:
        return result
    if line.startswith("/"):
        print(styles.style(styles.StyleToken.ERROR, f"unknown chat command: {line}"))
        print_chat_help()
        return ChatCommandResult(handled=True)

    return ChatCommandResult(handled=False)
