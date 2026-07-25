from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from openminion.base.config.bootstrap import bootstrap_config_path
from openminion.base.config.core import resolve_default_agent_id
from openminion.cli.presentation.json_output import print_json_payload
from openminion.cli.transport.runtime_source import call_daemon_or_inproc
from openminion.cli.transport.daemon_client import (
    daemon_request,
)
from openminion.cli.config import load_cli_config_from_args
from openminion.cli.parser.flags import (
    add_json_output_flag,
    add_profile_selector,
    add_runtime_source_flag,
)
from openminion.api.turns import run_turn

load_config = load_cli_config_from_args


def run_openminion(args: Any) -> int:
    message = _resolve_message(args)
    if not message:
        raise RuntimeError("Prompt is required (positional message or --file).")

    config = _load_run_config(args)
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
        setattr(args, "runtime_source", "inproc")

    path = "/v1/turn/stream" if bool(getattr(args, "stream", False)) else "/v1/turn"
    result = call_daemon_or_inproc(
        args=args,
        auto_start=auto_start,
        daemon_call=lambda endpoint: daemon_request(
            endpoint=endpoint,
            method="POST",
            path=path,
            payload=request_payload,
            timeout_s=60,
        ),
        inproc_call=lambda: _run_inproc(args, request_payload),
    )
    response = dict(result.payload)
    response.setdefault("runtime_source", result.source)
    if result.fallback_reason:
        response.setdefault("runtime_fallback_reason", result.fallback_reason)
    if not response.get("ok", False):
        raise RuntimeError(_format_api_error(response, 500))
    _print_output(args, response)
    return 0


def _run_inproc(
    args: Any,
    request_payload: dict[str, Any],
) -> dict[str, Any]:
    turn_payload = {
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
    }
    if getattr(args, "home_root", None) or getattr(args, "data_root", None):
        from openminion.api.runtime import APIRuntime

        runtime = APIRuntime.from_config_path(
            getattr(args, "config", None),
            home_root=getattr(args, "home_root", None),
            data_root=getattr(args, "data_root", None),
        )
        try:
            turn = run_turn(
                config_path=args.config,
                payload=turn_payload,
                runtime=runtime,
            )
        finally:
            runtime.close()
    else:
        turn = run_turn(
            config_path=args.config,
            payload=turn_payload,
        )
    return {"ok": True, "turn": turn, "trace_id": str(turn.get("run_id", "")).strip()}


def _load_run_config(args: Any) -> Any:
    loader = globals().get("load_config", load_cli_config_from_args)
    if loader is load_cli_config_from_args:
        return load_cli_config_from_args(args)
    return loader(getattr(args, "config", None))


def _print_output(args: Any, payload: dict[str, Any]) -> None:
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


def _resolve_message(args: Any) -> str:
    from_file = str(getattr(args, "file", "") or "").strip()
    if from_file:
        path = Path(from_file).expanduser().resolve()
        if not path.exists():
            raise RuntimeError(f"Prompt file not found: {path}")
        return path.read_text(encoding="utf-8").strip()
    return str(getattr(args, "prompt", "") or "").strip()


def _format_api_error(payload: dict[str, Any], status: int) -> str:
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
    add_profile_selector(run, dest="agent", help_text="Configured profile id")
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
    add_runtime_source_flag(run)
    from openminion.cli.ux.verbosity import (
        add_progress_flag,
        add_verbosity_flag,
    )

    add_verbosity_flag(run)
    add_progress_flag(run, include_aliases=True)
    add_json_output_flag(run)
    run.set_defaults(handler=run_openminion, needs_app=False)
