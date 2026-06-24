from __future__ import annotations

import argparse
import sys
from typing import Any

from openminion.api.runtime import APIRuntime
from openminion.cli.parser.flags import add_json_output_flag
from openminion.cli.presentation.json_output import print_json_payload


def run_sessions_list(args) -> int:
    agent_filter = str(getattr(args, "agent", "") or "").strip().lower()
    status_filter = str(getattr(args, "status", "") or "").strip().lower()
    channel_filter = str(getattr(args, "channel", "") or "").strip().lower()
    limit = max(1, int(getattr(args, "limit", 50) or 50))
    output_json = bool(getattr(args, "output_json", False))

    try:
        runtime = APIRuntime.from_config_path(
            getattr(args, "config", None),
            home_root=getattr(args, "home_root", None),
            data_root=getattr(args, "data_root", None),
        )
    except Exception as exc:
        print(f"openminion sessions: startup error — {exc}", file=sys.stderr)
        return 1

    try:
        rows = _build_rows(
            runtime,
            agent_filter=agent_filter,
            status_filter=status_filter,
            channel_filter=channel_filter,
            limit=limit,
        )
    finally:
        runtime.close()

    if output_json:
        print_json_payload(rows)
        return 0

    _print_table(rows)
    return 0


def _build_rows(
    runtime: Any,
    *,
    agent_filter: str,
    status_filter: str = "",
    channel_filter: str = "",
    limit: int,
) -> list[dict[str, Any]]:
    from openminion.modules.storage.runtime.session_store import (
        agent_id_from_session_key,
    )

    try:
        sessions = runtime.sessions.list_sessions(
            limit=limit,
            agent_id=agent_filter or None,
            status=status_filter or None,
            channel=channel_filter or None,
        )
    except Exception as exc:
        print(f"openminion sessions: could not list sessions — {exc}", file=sys.stderr)
        return []

    rows: list[dict[str, Any]] = []
    for session in sessions:
        agent_id = agent_id_from_session_key(session.session_key)
        if not agent_id:
            agent_id = str(getattr(session, "active_agent_id", "") or "").strip()
        if agent_filter and not agent_id.lower().startswith(agent_filter):
            continue

        name = str(session.metadata.get("name", "")).strip()
        try:
            turn_count = runtime.sessions.count_messages(session_id=session.id)
        except Exception:
            turn_count = 0

        rows.append(
            {
                "id": session.id,
                "name": name,
                "agent": agent_id,
                "channel": session.channel,
                "turns": turn_count,
                "age": _age_label(session.updated_at),
                "status": session.status,
            }
        )
        if len(rows) >= limit:
            break

    return rows


def run_sessions_delete(args) -> int:
    session_id = str(getattr(args, "session_id", "") or "").strip()
    if not session_id:
        print("openminion sessions: missing session id", file=sys.stderr)
        return 2

    assume_yes = bool(getattr(args, "yes", False))
    if not assume_yes:
        confirmation = input(
            f"Delete session '{session_id}' permanently? [y/N] "
        ).strip()
        if confirmation.lower() not in {"y", "yes"}:
            print("Cancelled.")
            return 1

    try:
        runtime = APIRuntime.from_config_path(
            getattr(args, "config", None),
            home_root=getattr(args, "home_root", None),
            data_root=getattr(args, "data_root", None),
        )
    except Exception as exc:
        print(f"openminion sessions: startup error — {exc}", file=sys.stderr)
        return 1

    try:
        deleted = bool(runtime.sessions.delete_session(session_id))
    except Exception as exc:
        print(f"openminion sessions: could not delete session — {exc}", file=sys.stderr)
        return 1
    finally:
        runtime.close()

    if not deleted:
        print(
            f"openminion sessions: session not found — {session_id}",
            file=sys.stderr,
        )
        return 1

    print(f"Deleted session {session_id}.")
    return 0


def _age_label(iso: str) -> str:
    from datetime import datetime, timezone

    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = int((datetime.now(timezone.utc) - dt).total_seconds())
    if delta < 3600:
        return f"{max(1, delta // 60)}m"
    if delta < 86400:
        return f"{delta // 3600}h"
    return f"{delta // 86400}d"


def _print_table(rows: list[dict[str, Any]]) -> None:
    if not rows:
        print("No sessions found.")
        return

    headers = ["ID", "NAME", "AGENT", "CHANNEL", "TURNS", "AGE", "STATUS"]
    col_widths = {h: len(h) for h in headers}
    field_map = {
        "ID": "id",
        "NAME": "name",
        "AGENT": "agent",
        "CHANNEL": "channel",
        "TURNS": "turns",
        "AGE": "age",
        "STATUS": "status",
    }
    for row in rows:
        for h in headers:
            col_widths[h] = max(col_widths[h], len(str(row[field_map[h]])))

    def _fmt_row(values: list[str]) -> str:
        parts = []
        for h, v in zip(headers, values):
            parts.append(v.ljust(col_widths[h]))
        return "  ".join(parts)

    print(_fmt_row(headers))
    print(_fmt_row(["-" * col_widths[h] for h in headers]))
    for row in rows:
        print(_fmt_row([str(row[field_map[h]]) for h in headers]))


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    sessions_cmd = subparsers.add_parser(
        "sessions", help="Session browser and management"
    )
    sessions_subcommands = sessions_cmd.add_subparsers(
        dest="sessions_command", required=True
    )
    sessions_list_cmd = sessions_subcommands.add_parser("list", help="List sessions")
    sessions_list_cmd.add_argument(
        "--agent", default=None, help="Filter by agent id prefix"
    )
    sessions_list_cmd.add_argument(
        "--status", default=None, help="Filter by session status"
    )
    sessions_list_cmd.add_argument(
        "--channel", default=None, help="Filter by session channel"
    )
    sessions_list_cmd.add_argument(
        "--limit", type=int, default=50, help="Max rows to return (default 50)"
    )
    add_json_output_flag(
        sessions_list_cmd, dest="output_json", help_text="Emit JSON array"
    )
    sessions_list_cmd.set_defaults(handler=run_sessions_list, needs_app=False)

    sessions_delete_cmd = sessions_subcommands.add_parser(
        "delete", help="Permanently delete a session"
    )
    sessions_delete_cmd.add_argument("session_id", help="Session id to delete")
    sessions_delete_cmd.add_argument(
        "--yes",
        action="store_true",
        help="Delete without prompting for confirmation",
    )
    sessions_delete_cmd.set_defaults(handler=run_sessions_delete, needs_app=False)
