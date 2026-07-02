from __future__ import annotations

import argparse
import sys


def run_room_create(args) -> int:
    from openminion.api.runtime import APIRuntime

    try:
        runtime = APIRuntime.from_config_path(
            getattr(args, "config", None),
            home_root=getattr(args, "home_root", None),
            data_root=getattr(args, "data_root", None),
        )
    except Exception as exc:
        print(f"openminion room: startup error — {exc}", file=sys.stderr)
        return 1

    try:
        metadata: dict[str, object] = {}
        name = str(getattr(args, "name", "") or "").strip()
        routing_mode = str(getattr(args, "routing_mode", "") or "").strip().lower()
        if name:
            metadata["name"] = name
        if routing_mode:
            metadata["room_routing_mode"] = routing_mode
        session = runtime.sessions.create_room(
            channel=str(getattr(args, "channel", "") or "").strip() or "cli",
            target=str(getattr(args, "target", "") or "").strip() or (name or "room"),
            metadata=metadata,
        )
        agent_ids = [
            str(agent_id).strip()
            for agent_id in getattr(args, "agents", []) or []
            if str(agent_id).strip()
        ]
        for index, agent_id in enumerate(agent_ids):
            runtime.sessions.add_participant(
                session_id=session.id,
                participant_type="agent",
                participant_id=agent_id,
                channel=session.channel,
                role="participant" if index else "owner",
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
    from openminion.api.runtime import APIRuntime

    session_id = str(getattr(args, "session_id", "") or "").strip()
    if not session_id:
        print("openminion room: missing session id", file=sys.stderr)
        return 2
    human_id = str(getattr(args, "human", "") or "").strip()
    agent_id = str(getattr(args, "agent", "") or "").strip()
    if not human_id and not agent_id:
        print("openminion room: use --human <id> or --agent <id>", file=sys.stderr)
        return 2

    try:
        runtime = APIRuntime.from_config_path(
            getattr(args, "config", None),
            home_root=getattr(args, "home_root", None),
            data_root=getattr(args, "data_root", None),
        )
    except Exception as exc:
        print(f"openminion room: startup error — {exc}", file=sys.stderr)
        return 1

    participant_type = "human" if human_id else "agent"
    participant_id = human_id or agent_id
    try:
        runtime.sessions.add_participant(
            session_id=session_id,
            participant_type=participant_type,
            participant_id=participant_id,
            channel="cli",
            role=str(getattr(args, "role", "") or "").strip().lower() or "participant",
            display_name=participant_id,
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
