from __future__ import annotations

import argparse
import json
from argparse import Namespace
from typing import Any

from openminion.cli.constants import CLI_DEFAULT_TIME_SESSION
from openminion.cli.parser.flags import add_tool_session_arg
from openminion.cli.commands.tools import run_tools


def run_time(args) -> int:
    action = str(getattr(args, "time_command", "") or "").strip().lower()
    if action == "now":
        return _dispatch(args, tool_name="time.now", payload=_payload_now(args))
    if action == "in-zone":
        return _dispatch(args, tool_name="time.in_zone", payload=_payload_in_zone(args))
    if action == "convert":
        return _dispatch(args, tool_name="time.convert", payload=_payload_convert(args))
    if action == "parse-iso":
        return _dispatch(
            args, tool_name="time.parse_iso", payload=_payload_parse_iso(args)
        )
    if action == "diff":
        return _dispatch(args, tool_name="time.diff", payload=_payload_diff(args))
    if action == "format":
        return _dispatch(args, tool_name="time.format", payload=_payload_format(args))
    if action == "start-of-day":
        return _dispatch(
            args, tool_name="time.start_of_day", payload=_payload_day_boundary(args)
        )
    if action == "end-of-day":
        return _dispatch(
            args, tool_name="time.end_of_day", payload=_payload_day_boundary(args)
        )
    if action == "next-cron":
        return _dispatch(
            args, tool_name="time.next_cron", payload=_payload_next_cron(args)
        )
    raise RuntimeError("Unknown time command")


def _dispatch(args, *, tool_name: str, payload: dict[str, Any]) -> int:
    proxy = Namespace(**vars(args))
    proxy.tools_command = "run"
    proxy.tool = tool_name
    proxy.json_payload = json.dumps(payload, sort_keys=True, ensure_ascii=True)
    proxy.session = str(getattr(args, "session", "") or "").strip() or "time-cli"
    return run_tools(proxy)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _payload_now(args) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    timezone_name = _clean(getattr(args, "timezone", ""))
    if timezone_name:
        payload["timezone"] = timezone_name
    return payload


def _payload_in_zone(args) -> dict[str, Any]:
    timezone_name = _clean(getattr(args, "timezone", ""))
    if not timezone_name:
        raise RuntimeError("--timezone is required")
    return {"timezone": timezone_name}


def _payload_convert(args) -> dict[str, Any]:
    iso = _clean(getattr(args, "iso", ""))
    timezone_name = _clean(getattr(args, "timezone", ""))
    if not iso:
        raise RuntimeError("--iso is required")
    if not timezone_name:
        raise RuntimeError("--timezone is required")
    return {
        "iso": iso,
        "to_timezone": timezone_name,
    }


def _payload_parse_iso(args) -> dict[str, Any]:
    iso = _clean(getattr(args, "iso", ""))
    timezone_hint = _clean(getattr(args, "timezone_hint", ""))
    if not iso:
        raise RuntimeError("--iso is required")
    payload: dict[str, Any] = {"iso": iso}
    if timezone_hint:
        payload["timezone_hint"] = timezone_hint
    return payload


def _payload_diff(args) -> dict[str, Any]:
    iso_a = _clean(getattr(args, "a", ""))
    iso_b = _clean(getattr(args, "b", ""))
    if not iso_a or not iso_b:
        raise RuntimeError("--a and --b are required")
    unit = _clean(getattr(args, "unit", "")) or "seconds"
    signed = bool(getattr(args, "signed", False))
    return {
        "a": iso_a,
        "b": iso_b,
        "unit": unit,
        "abs": not signed,
    }


def _payload_format(args) -> dict[str, Any]:
    iso = _clean(getattr(args, "iso", ""))
    if not iso:
        raise RuntimeError("--iso is required")
    payload: dict[str, Any] = {
        "iso": iso,
        "format": _clean(getattr(args, "format", "")) or "iso",
    }
    timezone_name = _clean(getattr(args, "timezone", ""))
    if timezone_name:
        payload["timezone"] = timezone_name
    custom = _clean(getattr(args, "custom", ""))
    if custom:
        payload["custom"] = custom
    return payload


def _payload_day_boundary(args) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    iso = _clean(getattr(args, "iso", ""))
    timezone_name = _clean(getattr(args, "timezone", ""))
    if iso:
        payload["iso"] = iso
    if timezone_name:
        payload["timezone"] = timezone_name
    return payload


def _payload_next_cron(args) -> dict[str, Any]:
    cron = _clean(getattr(args, "cron", ""))
    timezone_name = _clean(getattr(args, "timezone", ""))
    if not cron:
        raise RuntimeError("--cron is required")
    if not timezone_name:
        raise RuntimeError("--timezone is required")
    payload: dict[str, Any] = {
        "cron": cron,
        "timezone": timezone_name,
        "count": int(getattr(args, "count", 3) or 3),
    }
    from_iso = _clean(getattr(args, "from_iso", ""))
    if from_iso:
        payload["from_iso"] = from_iso
    return payload


def _add_timezone_arg(
    parser: argparse.ArgumentParser,
    *,
    required: bool,
    default: str | None,
    help_text: str,
) -> None:
    parser.add_argument(
        "--timezone",
        "--tz",
        dest="timezone",
        required=required,
        default=default,
        help=help_text,
    )


def _finalize_time_subcommand(parser: argparse.ArgumentParser) -> None:
    add_tool_session_arg(parser, default=CLI_DEFAULT_TIME_SESSION)
    parser.set_defaults(handler=run_time, needs_app=False)


def _register_time_now_subcommand(time_subcommands) -> None:
    parser = time_subcommands.add_parser("now", help="Get current UTC/local instant")
    _add_timezone_arg(
        parser,
        required=False,
        default=None,
        help_text="IANA timezone (default: identity timezone or UTC)",
    )
    _finalize_time_subcommand(parser)


def _register_time_in_zone_subcommand(time_subcommands) -> None:
    parser = time_subcommands.add_parser(
        "in-zone", help="Get current instant in a timezone"
    )
    _add_timezone_arg(parser, required=True, default=None, help_text="IANA timezone")
    _finalize_time_subcommand(parser)


def _register_time_convert_subcommand(time_subcommands) -> None:
    parser = time_subcommands.add_parser(
        "convert", help="Convert ISO timestamp to timezone"
    )
    parser.add_argument("--iso", required=True, help="ISO8601 timestamp")
    _add_timezone_arg(
        parser, required=True, default=None, help_text="Destination IANA timezone"
    )
    _finalize_time_subcommand(parser)


def _register_time_parse_iso_subcommand(time_subcommands) -> None:
    parser = time_subcommands.add_parser(
        "parse-iso", help="Validate and normalize ISO timestamp"
    )
    parser.add_argument("--iso", required=True, help="ISO8601 timestamp")
    parser.add_argument(
        "--timezone-hint",
        default=None,
        help="Timezone hint when ISO has no offset",
    )
    _finalize_time_subcommand(parser)


def _register_time_diff_subcommand(time_subcommands) -> None:
    parser = time_subcommands.add_parser(
        "diff", help="Compute delta between two timestamps"
    )
    parser.add_argument("--a", required=True, help="Start ISO8601 timestamp")
    parser.add_argument("--b", required=True, help="End ISO8601 timestamp")
    parser.add_argument(
        "--unit",
        default="seconds",
        choices=["seconds", "minutes", "hours", "days"],
        help="Output unit (default: seconds)",
    )
    parser.add_argument(
        "--signed",
        action="store_true",
        help="Return signed delta (default: absolute)",
    )
    _finalize_time_subcommand(parser)


def _register_time_format_subcommand(time_subcommands) -> None:
    parser = time_subcommands.add_parser("format", help="Format an ISO timestamp")
    parser.add_argument("--iso", required=True, help="ISO8601 timestamp")
    _add_timezone_arg(parser, required=False, default=None, help_text="IANA timezone")
    parser.add_argument(
        "--format",
        dest="format",
        default="iso",
        choices=["iso", "rfc3339", "date", "time", "datetime", "custom"],
        help="Output format",
    )
    parser.add_argument(
        "--custom",
        default=None,
        help="strftime pattern (required for --format custom)",
    )
    _finalize_time_subcommand(parser)


def _register_time_day_boundary_subcommand(
    time_subcommands, *, name: str, help_text: str
) -> None:
    parser = time_subcommands.add_parser(name, help=help_text)
    parser.add_argument("--iso", default=None, help="Reference ISO8601 timestamp")
    _add_timezone_arg(
        parser,
        required=False,
        default=None,
        help_text="IANA timezone (default: identity timezone or UTC)",
    )
    _finalize_time_subcommand(parser)


def _register_time_next_cron_subcommand(time_subcommands) -> None:
    parser = time_subcommands.add_parser(
        "next-cron", help="Compute next cron run instants"
    )
    parser.add_argument("--cron", required=True, help="5-field cron expression")
    _add_timezone_arg(parser, required=True, default=None, help_text="IANA timezone")
    parser.add_argument(
        "--from-iso",
        default=None,
        help="Start timestamp (default: now)",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=3,
        help="Number of upcoming runs (default: 3, max: 50)",
    )
    _finalize_time_subcommand(parser)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    time_cmd = subparsers.add_parser("time", help="Trusted time helpers")
    time_subcommands = time_cmd.add_subparsers(dest="time_command")

    _register_time_now_subcommand(time_subcommands)
    _register_time_in_zone_subcommand(time_subcommands)
    _register_time_convert_subcommand(time_subcommands)
    _register_time_parse_iso_subcommand(time_subcommands)
    _register_time_diff_subcommand(time_subcommands)
    _register_time_format_subcommand(time_subcommands)
    _register_time_day_boundary_subcommand(
        time_subcommands, name="start-of-day", help_text="Compute start-of-day instant"
    )
    _register_time_day_boundary_subcommand(
        time_subcommands, name="end-of-day", help_text="Compute end-of-day instant"
    )
    _register_time_next_cron_subcommand(time_subcommands)
