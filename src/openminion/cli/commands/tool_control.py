from __future__ import annotations

import argparse
from typing import Any

from openminion.cli.presentation.json_output import print_json_payload


def run_toolctl(args: Any, app: Any) -> int:
    service = getattr(app, "authored_tools", None)
    if service is None:
        print_json_payload(
            {
                "ok": False,
                "error": {
                    "code": "AUTHORED_TOOLS_UNAVAILABLE",
                    "message": "Authored tool service is not available in this runtime.",
                },
            }
        )
        return 1

    action = str(getattr(args, "toolctl_command", "") or "").strip().lower()
    if action == "list":
        payload = {
            "ok": True,
            "tools": service.list_authored_tools(
                tier=str(getattr(args, "tier", "all") or "all"),
                include_removed=bool(getattr(args, "include_removed", False)),
            ),
        }
    elif action == "get":
        detail = service.get_authored_tool_detail(str(args.tool_name))
        payload = (
            {"ok": True, "tool": detail}
            if detail is not None
            else {
                "ok": False,
                "error": {"code": "TOOL_NOT_FOUND", "message": str(args.tool_name)},
            }
        )
    elif action == "promote":
        payload = service.promote_tool(
            str(args.tool_name),
            force=bool(getattr(args, "force", False)),
            actor_id="toolctl",
        )
    elif action == "set-scope":
        payload = service.set_tool_scope(
            str(args.tool_name),
            scope=str(args.scope),
            actor_id="toolctl",
        )
    elif action == "remove":
        payload = service.remove_tool(
            str(args.tool_name),
            actor_id="toolctl",
            reason=str(getattr(args, "reason", "") or "").strip() or None,
        )
    else:
        raise RuntimeError("Unknown toolctl command")

    print_json_payload(payload)
    return 0 if payload.get("ok", False) else 1


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    toolctl = subparsers.add_parser(
        "toolctl",
        help="Operator controls for authored tools",
    )
    toolctl_subcommands = toolctl.add_subparsers(dest="toolctl_command")

    list_cmd = toolctl_subcommands.add_parser("list", help="List authored tools")
    list_cmd.add_argument(
        "--tier",
        default="all",
        choices=("experimental", "trusted", "all"),
    )
    list_cmd.add_argument("--include-removed", action="store_true")
    list_cmd.set_defaults(handler=run_toolctl, needs_app=True)

    get_cmd = toolctl_subcommands.add_parser("get", help="Get one authored tool")
    get_cmd.add_argument("tool_name")
    get_cmd.set_defaults(handler=run_toolctl, needs_app=True)

    promote_cmd = toolctl_subcommands.add_parser(
        "promote",
        help="Promote one authored tool to trusted (daemon restart required for live registry refresh).",
    )
    promote_cmd.add_argument("tool_name")
    promote_cmd.add_argument("--force", action="store_true")
    promote_cmd.set_defaults(handler=run_toolctl, needs_app=True)

    scope_cmd = toolctl_subcommands.add_parser(
        "set-scope",
        help="Change one authored tool scope (daemon restart required for live registry refresh).",
    )
    scope_cmd.add_argument("tool_name")
    scope_cmd.add_argument(
        "--scope",
        required=True,
        choices=("READ_ONLY", "WRITE_SAFE", "POWER_USER", "UI_AUTOMATION"),
    )
    scope_cmd.set_defaults(handler=run_toolctl, needs_app=True)

    remove_cmd = toolctl_subcommands.add_parser(
        "remove",
        help="Soft-delete one authored tool (daemon restart required for live registry refresh).",
    )
    remove_cmd.add_argument("tool_name")
    remove_cmd.add_argument("--reason", default=None)
    remove_cmd.set_defaults(handler=run_toolctl, needs_app=True)
