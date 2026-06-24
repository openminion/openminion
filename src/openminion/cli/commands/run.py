from __future__ import annotations

import argparse
from pathlib import Path

from openminion.base.config.bootstrap import bootstrap_config_path
from openminion.base.config.core import resolve_default_agent_id
from openminion.cli.presentation.json_output import print_json_payload
from openminion.cli.commands.daemon import ensure_daemon_running
from openminion.cli.transport.daemon_client import (
    daemon_request,
)
from openminion.cli.bootstrap.loader import load_config
from openminion.cli.parser.flags import add_json_output_flag
from openminion.api.turns import run_turn


def run_openminion(args) -> int:
    message = _resolve_message(args)
    if not message:
        raise RuntimeError("Prompt is required (positional message or --file).")

    config = load_config(args.config)
    if getattr(args, "config", None):
        bootstrap_config_path(Path(args.config).expanduser())
    mode = str(config.runtime.process_mode or "daemon").strip().lower()
    auto_start = bool(config.runtime.daemon_auto_start)

    request_payload = {
        "message": message,
        "input_text": message,
        "agent_id": str(getattr(args, "agent", "") or "").strip()
        or resolve_default_agent_id(config),
        "session_id": str(getattr(args, "session", "") or "").strip() or "cli-run",
        "channel": "console",
        "target": "cli-user",
        "deliver": False,
        "meta": {
            "purpose": str(getattr(args, "purpose", "") or "").strip(),
            "source": "openminion.run",
            "resume": str(bool(getattr(args, "resume", False))).lower(),
            "reset_session": str(bool(getattr(args, "reset_session", False))).lower(),
        },
    }

    if mode == "single-process":
        turn = _run_inproc(args, request_payload)
        _print_output(args, turn)
        return 0

    try:
        endpoint = ensure_daemon_running(args.config, auto_start=auto_start)
        path = "/v1/turn/stream" if bool(getattr(args, "stream", False)) else "/v1/turn"
        status, response = daemon_request(
            endpoint=endpoint,
            method="POST",
            path=path,
            payload=request_payload,
            timeout_s=60,
        )
        if status >= 400 or not response.get("ok", False):
            raise RuntimeError(_format_api_error(response, status))
        _print_output(args, response)
        return 0
    except RuntimeError:
        turn = _run_inproc(args, request_payload)
        _print_output(args, turn)
        return 0


def _run_inproc(args, request_payload: dict) -> dict:
    turn = run_turn(
        config_path=args.config,
        payload={
            "message": request_payload["message"],
            "agent_id": request_payload["agent_id"],
            "session_id": request_payload["session_id"],
            "channel": request_payload["channel"],
            "target": request_payload["target"],
            "deliver": bool(request_payload.get("deliver", False)),
            "inbound_metadata": {
                "resume": request_payload.get("meta", {}).get("resume", "false"),
                "reset_session": request_payload.get("meta", {}).get(
                    "reset_session", "false"
                ),
            },
        },
    )
    return {"ok": True, "turn": turn, "trace_id": str(turn.get("run_id", "")).strip()}


def _print_output(args, payload: dict) -> None:
    if bool(getattr(args, "json", False)):
        print_json_payload(payload)
        return

    turn = payload.get("turn") if isinstance(payload, dict) else None
    if isinstance(turn, dict):
        text = (
            str(turn.get("final_text", "")).strip() or str(turn.get("body", "")).strip()
        )
        if text:
            print(text)
            return
    print_json_payload(payload)


def _resolve_message(args) -> str:
    from_file = str(getattr(args, "file", "") or "").strip()
    if from_file:
        path = Path(from_file).expanduser().resolve()
        if not path.exists():
            raise RuntimeError(f"Prompt file not found: {path}")
        return path.read_text(encoding="utf-8").strip()
    return str(getattr(args, "prompt", "") or "").strip()


def _format_api_error(payload: dict, status: int) -> str:
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = str(error.get("message", "")).strip()
            if message:
                return f"daemon request failed ({status}): {message}"
    return f"daemon request failed ({status})"


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    run = subparsers.add_parser(
        "run", help="Run one turn through daemon or in-process runtime"
    )
    run.add_argument("prompt", nargs="?", default="", help="Prompt text")
    run.add_argument("--agent", default=None, help="Agent id")
    run.add_argument("--session", default=None, help="Session id")
    run.add_argument(
        "--resume",
        action="store_true",
        help="Force reuse of the latest resolved thread even if settled",
    )
    run.add_argument(
        "--reset-session",
        action="store_true",
        help="Force creation of a fresh thread for this session",
    )
    run.add_argument("--purpose", default="", help="Optional purpose tag")
    run.add_argument("--file", default="", help="Read prompt body from file")
    run.add_argument("--stream", action="store_true", help="Use /v1/turn/stream")
    from openminion.cli.ux.verbosity import (
        add_progress_flag,
        add_verbosity_flag,
    )

    add_verbosity_flag(run)
    add_progress_flag(run, include_aliases=True)
    add_json_output_flag(run)
    run.set_defaults(handler=run_openminion, needs_app=False)
