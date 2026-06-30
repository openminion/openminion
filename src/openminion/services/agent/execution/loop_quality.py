import json
from collections.abc import MutableMapping
from typing import Any

from openminion.tools.exec.command_parser import CommandParseError, parse_command


def _canonical_arguments(arguments: Any) -> str:
    try:
        return json.dumps(
            dict(arguments or {}),
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    except Exception:
        return str(arguments or "")


def tool_call_signature(call: Any) -> str:
    return json.dumps(
        {
            "arguments": _canonical_arguments(getattr(call, "arguments", {}) or {}),
            "name": str(getattr(call, "name", "") or "").strip(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def exec_tool_call_command(call: Any) -> str:
    tool_name = str(getattr(call, "name", "") or "").strip()
    if tool_name != "exec.run":
        return ""
    args = dict(getattr(call, "arguments", {}) or {})
    return str(args.get("command") or args.get("cmd") or "").strip()


def exec_command_action_class(command: str) -> str:
    if not command:
        return "tool_call"
    try:
        parsed = parse_command(command)
    except CommandParseError:
        return "unsupported_command_syntax"
    if len(parsed.segments) != 1 or parsed.operators:
        return "compound_command"
    argv = tuple(str(arg).strip() for arg in parsed.segments[0].argv if str(arg).strip())
    if not argv:
        return "tool_call"
    executable = argv[0].lower()
    if executable == "command" and len(argv) == 3 and argv[1] == "-v":
        return "discovery"
    if executable == "which" and len(argv) == 2:
        return "discovery"
    if len(argv) == 2 and argv[1] == "--version":
        return "version"
    return "tool_call"


def _event_kind(
    *,
    action_class: str,
    batch_count: int,
    turn_count: int,
) -> str | None:
    if batch_count > 1:
        return "duplicate_tool_call_observed"
    if turn_count > 1 and action_class in {"discovery", "version"}:
        return "redundant_discovery_version_observed"
    if turn_count > 1:
        return "repeated_tool_call_shape_observed"
    return None


def observe_tool_calls(
    tool_calls: list[Any],
    *,
    seen_signatures: MutableMapping[str, int] | None = None,
) -> list[dict[str, str]]:
    observations: list[dict[str, str]] = []
    batch_counts: dict[str, int] = {}
    signature_counts = seen_signatures if seen_signatures is not None else {}
    for call in list(tool_calls or []):
        signature = tool_call_signature(call)
        batch_count = batch_counts.get(signature, 0) + 1
        batch_counts[signature] = batch_count
        turn_count = int(signature_counts.get(signature, 0) or 0) + 1
        signature_counts[signature] = turn_count

        command = exec_tool_call_command(call)
        action_class = exec_command_action_class(command)
        event_kind = _event_kind(
            action_class=action_class,
            batch_count=batch_count,
            turn_count=turn_count,
        )
        if event_kind is None:
            continue
        observations.append(
            {
                "kind": "tool_loop_observation",
                "event_kind": event_kind,
                "tool_name": str(getattr(call, "name", "") or "").strip(),
                "call_id": str(getattr(call, "id", "") or "").strip(),
                "signature": signature,
                "action_class": action_class,
                "batch_count": str(batch_count),
                "turn_count": str(turn_count),
                "command": command,
            }
        )
    return observations


__all__ = [
    "exec_command_action_class",
    "exec_tool_call_command",
    "observe_tool_calls",
    "tool_call_signature",
]
