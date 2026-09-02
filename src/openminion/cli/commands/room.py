from __future__ import annotations

import argparse
import sys

from openminion.modules.storage import (
    is_room_session_key,
    normalize_identity,
    normalize_participant_role,
)


def _open_room_runtime(args):
    from openminion.api.runtime import APIRuntime

    return APIRuntime.from_config_path(
        getattr(args, "config", None),
        home_root=getattr(args, "home_root", None),
        data_root=getattr(args, "data_root", None),
    )


def run_room_create(args) -> int:
    try:
        runtime = _open_room_runtime(args)
    except Exception as exc:
        print(f"openminion room: startup error — {exc}", file=sys.stderr)
        return 1

    try:
        name = str(getattr(args, "name", "") or "").strip()
        routing_mode = str(getattr(args, "routing_mode", "") or "").strip().lower()
        agent_ids = [
            str(agent_id).strip() for agent_id in getattr(args, "agents", []) or []
        ]
        human_ids = [
            normalize_identity(str(human_id))
            for human_id in getattr(args, "humans", []) or []
        ]
        if any(not agent_id for agent_id in agent_ids):
            raise ValueError("agent id is required")
        if any(not human_id for human_id in human_ids):
            raise ValueError("human id is required")
        if len(set(human_ids)) != len(human_ids):
            raise ValueError("duplicate human id")
        if len(set(agent_ids)) != len(agent_ids):
            raise ValueError("duplicate agent id")
        unknown_agents = [
            agent_id for agent_id in agent_ids if agent_id not in runtime.config.agents
        ]
        if unknown_agents:
            raise ValueError(f"Unknown configured agent: {unknown_agents[0]}")

        metadata: dict[str, object] = {}
        if name:
            metadata["name"] = name
        if routing_mode:
            metadata["room_routing_mode"] = routing_mode
        if human_ids:
            metadata["local_human_id"] = human_ids[0]
        session = runtime.sessions.create_room(
            channel=str(getattr(args, "channel", "") or "").strip() or "cli",
            target=str(getattr(args, "target", "") or "").strip() or (name or "room"),
            metadata=metadata,
        )
        for index, human_id in enumerate(human_ids):
            runtime.sessions.add_participant(
                session_id=session.id,
                participant_type="human",
                participant_id=human_id,
                channel=session.channel,
                role="owner" if index == 0 else "participant",
                display_name=human_id,
            )
        for index, agent_id in enumerate(agent_ids):
            runtime.sessions.add_participant(
                session_id=session.id,
                participant_type="agent",
                participant_id=agent_id,
                channel=session.channel,
                role="participant" if human_ids or index else "owner",
                display_name=agent_id,
            )
        if agent_ids:
            runtime.sessions.set_active_agent(
                session_id=session.id, agent_id=agent_ids[0]
            )
        active_agent_id = runtime.sessions.get_active_agent(session.id) or ""
        participant_count = len(runtime.sessions.list_participants(session.id))
    except Exception as exc:
        print(f"openminion room: create failed — {exc}", file=sys.stderr)
        return 1
    finally:
        runtime.close()

    print(f"room={session.id}")
    print(f"participants={participant_count}")
    print(f"active_agent={active_agent_id}")
    return 0


def run_room_invite(args) -> int:
    session_id = str(getattr(args, "session_id", "") or "").strip()
    if not session_id:
        print("openminion room: missing session id", file=sys.stderr)
        return 2
    human_id = normalize_identity(str(getattr(args, "human", "") or ""))
    agent_id = str(getattr(args, "agent", "") or "").strip()
    if bool(human_id) == bool(agent_id):
        print(
            "openminion room: use exactly one of --human <id> or --agent <id>",
            file=sys.stderr,
        )
        return 2
    try:
        role = normalize_participant_role(
            str(getattr(args, "role", "") or "participant")
        )
    except ValueError as exc:
        print(f"openminion room: {exc}", file=sys.stderr)
        return 2
    if agent_id and role != "participant":
        print("openminion room: agent role must be participant", file=sys.stderr)
        return 2

    try:
        runtime = _open_room_runtime(args)
    except Exception as exc:
        print(f"openminion room: startup error — {exc}", file=sys.stderr)
        return 1

    try:
        session = runtime.sessions.get_session(session_id)
        if session is None or not is_room_session_key(session.session_key):
            raise ValueError(f"Room not found: {session_id}")
        if agent_id and agent_id not in runtime.config.agents:
            raise ValueError(f"Unknown configured agent: {agent_id}")
        participant_type = "human" if human_id else "agent"
        participant_id = human_id or agent_id
        runtime.sessions.add_participant(
            session_id=session_id,
            participant_type=participant_type,
            participant_id=participant_id,
            channel=session.channel,
            role=role,
            display_name=participant_id,
        )
        if (
            participant_type == "human"
            and role == "owner"
            and not str(session.metadata.get("local_human_id", "") or "").strip()
        ):
            runtime.sessions.update_session_metadata(
                session_id=session_id,
                patch={"local_human_id": participant_id},
            )
        participant_count = len(runtime.sessions.list_participants(session_id))
    except Exception as exc:
        print(f"openminion room: invite failed — {exc}", file=sys.stderr)
        return 1
    finally:
        runtime.close()

    print(f"invited {participant_type}={participant_id}")
    print(f"participants={participant_count}")
    return 0


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    room_cmd = subparsers.add_parser("room", help="Create and manage room sessions")
    room_subcommands = room_cmd.add_subparsers(dest="room_command", required=True)

    room_create_cmd = room_subcommands.add_parser("create", help="Create a room")
    room_create_cmd.add_argument("--name", default="", help="Room display name")
    room_create_cmd.add_argument(
        "--agent",
        dest="agents",
        action="append",
        default=[],
        help="Agent participant id (repeatable)",
    )
    room_create_cmd.add_argument(
        "--human",
        dest="humans",
        action="append",
        default=[],
        help="Human participant id (repeatable)",
    )
    room_create_cmd.add_argument(
        "--channel",
        default="cli",
        help="Creator channel recorded on the room",
    )
    room_create_cmd.add_argument(
        "--target",
        default="room",
        help="Creator target recorded on the room",
    )
    room_create_cmd.add_argument(
        "--routing-mode",
        choices=("addressed", "broadcast", "sequential"),
        default="addressed",
        help="Room routing mode",
    )
    room_create_cmd.set_defaults(handler=run_room_create, needs_app=False)

    room_invite_cmd = room_subcommands.add_parser(
        "invite",
        help="Invite a participant into an existing room",
    )
    room_invite_cmd.add_argument("session_id", help="Room session id")
    room_invite_cmd.add_argument("--human", default="", help="Human participant id")
    room_invite_cmd.add_argument("--agent", default="", help="Agent participant id")
    room_invite_cmd.add_argument(
        "--role", default="participant", help="Participant role"
    )
    room_invite_cmd.set_defaults(handler=run_room_invite, needs_app=False)
