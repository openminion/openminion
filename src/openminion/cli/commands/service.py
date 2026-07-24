from __future__ import annotations

import argparse
import logging
from typing import Any

from openminion.cli.config import load_cli_config_from_args
from openminion.cli.parser.flags import add_json_output_flag
from openminion.cli.presentation.json_output import print_json_payload

_SERVICE_IDS = ("daemon", "api", "gateway", "cron", "sidecar")


def run_service(args: Any) -> int:
    action = str(getattr(args, "service_command", "") or "").strip().lower()
    if action == "list":
        return _service_list(args)
    if action == "status":
        return _service_status(args)
    if action in {"start", "stop", "restart", "logs"}:
        return _service_lifecycle(args, action=action)
    raise RuntimeError("Unknown service command")


def _service_list(args: Any) -> int:
    payload = {
        "ok": True,
        "services": [_service_descriptor(service_id) for service_id in _SERVICE_IDS],
    }
    _print_service_payload(payload, as_json=bool(getattr(args, "json", False)))
    return 0


def _service_status(args: Any) -> int:
    service_id = _service_arg(args)
    services = _SERVICE_IDS if not service_id else (service_id,)
    _validate_service_ids(services)
    payload = {
        "ok": True,
        "services": [
            _service_status_payload(args, service_id=item) for item in services
        ],
    }
    _print_service_payload(payload, as_json=bool(getattr(args, "json", False)))
    return 0


def _service_lifecycle(args: Any, *, action: str) -> int:
    service_id = _service_arg(args)
    if not service_id:
        raise RuntimeError(f"service {action} requires a service id")
    _validate_service_ids((service_id,))
    if service_id == "daemon":
        return _run_daemon_lifecycle(args, action=action)
    if service_id == "sidecar":
        return _run_sidecar_lifecycle(args, action=action)
    message = (
        f"service {service_id} does not have a background {action} owner yet. "
        f"Use `openminion {service_id} --help` for its direct command surface."
    )
    payload = {"ok": False, "service": service_id, "action": action, "message": message}
    _print_service_payload(payload, as_json=bool(getattr(args, "json", False)))
    return 1


def _run_daemon_lifecycle(args: Any, *, action: str) -> int:
    from openminion.cli.commands import daemon as daemon_command

    kwargs = {
        "home_root": getattr(args, "home_root", None),
        "data_root": getattr(args, "data_root", None),
    }
    if action == "start":
        return int(
            daemon_command.daemon_start(getattr(args, "config", None), **kwargs) or 0
        )
    if action == "stop":
        return int(
            daemon_command.daemon_stop(getattr(args, "config", None), **kwargs) or 0
        )
    if action == "restart":
        return int(
            daemon_command.daemon_restart(getattr(args, "config", None), **kwargs) or 0
        )
    if action == "logs":
        return int(
            daemon_command.daemon_logs(
                getattr(args, "config", None),
                lines=int(getattr(args, "lines", 200) or 200),
                follow=bool(getattr(args, "follow", False)),
                **kwargs,
            )
            or 0
        )
    raise RuntimeError(f"unsupported daemon lifecycle action: {action}")


def _run_sidecar_lifecycle(args: Any, *, action: str) -> int:
    from argparse import Namespace

    from openminion.cli.commands.sidecar import run_sidecar

    if action == "logs":
        payload = {
            "ok": False,
            "service": "sidecar",
            "action": action,
            "message": "sidecar logs are not exposed by the sidecar manager yet",
        }
        _print_service_payload(payload, as_json=bool(getattr(args, "json", False)))
        return 1
    sidecar_name = str(getattr(args, "sidecar", "") or "").strip() or "pinchtab"
    sidecar_args = Namespace(
        **vars(args),
        sidecar_command=action,
        name=sidecar_name,
        yes=bool(getattr(args, "yes", False)),
        no_prompt=bool(getattr(args, "no_prompt", False)),
        kill=bool(getattr(args, "kill", False)),
    )
    return int(run_sidecar(sidecar_args) or 0)


def _service_status_payload(args: Any, *, service_id: str) -> dict[str, Any]:
    descriptor = _service_descriptor(service_id)
    if service_id == "daemon":
        from openminion.cli.commands.daemon import _build_daemon_status_payload

        try:
            return {
                **descriptor,
                **_build_daemon_status_payload(
                    getattr(args, "config", None),
                    home_root=getattr(args, "home_root", None),
                    data_root=getattr(args, "data_root", None),
                ),
            }
        except RuntimeError as exc:
            return {**descriptor, "ok": False, "status": "error", "message": str(exc)}
    if service_id == "sidecar":
        try:
            from argparse import Namespace

            from openminion.cli.commands.sidecar import (
                _build_manager,
                _collect_statuses,
            )

            config = load_cli_config_from_args(args)
            manager = _build_manager(
                Namespace(**vars(args)),
                config,
                logger=logging.getLogger("openminion.sidecars"),
            )
            return {
                **descriptor,
                "ok": True,
                "status": "configured",
                "sidecars": _collect_statuses(manager, name=None),
            }
        except RuntimeError as exc:
            return {**descriptor, "ok": False, "status": "error", "message": str(exc)}
    return {
        **descriptor,
        "ok": True,
        "status": "command-surface",
        "lifecycle": "foreground",
    }


def _service_descriptor(service_id: str) -> dict[str, str]:
    return {
        "id": service_id,
        "command": _service_command(service_id),
        "description": {
            "daemon": "Background OpenMinion runtime daemon",
            "api": "Foreground HTTP API server",
            "gateway": "Foreground gateway turn runner",
            "cron": "Scheduled task operator surface",
            "sidecar": "Managed helper process such as PinchTab",
        }[service_id],
    }


def _service_command(service_id: str) -> str:
    if service_id == "api":
        return "openminion api run"
    return f"openminion {service_id}"


def _service_arg(args: Any) -> str:
    return str(getattr(args, "service", "") or "").strip().lower()


def _validate_service_ids(service_ids: tuple[str, ...]) -> None:
    unknown = [item for item in service_ids if item not in _SERVICE_IDS]
    if unknown:
        raise RuntimeError(f"Unknown service: {unknown[0]}")


def _print_service_payload(payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print_json_payload(payload)
        return
    if "services" in payload:
        services = payload.get("services", [])
        print(f"services: count={len(services) if isinstance(services, list) else 0}")
        for item in services if isinstance(services, list) else []:
            if isinstance(item, dict):
                print(
                    f"- {item.get('id', 'unknown')}: "
                    f"status={item.get('status', 'available')} "
                    f"command={item.get('command', '')}"
                )
        return
    print(f"service {payload.get('service', '-')}: {payload.get('message', '')}")


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    service = subparsers.add_parser(
        "service",
        help="Unified service lifecycle overview",
    )
    service_subcommands = service.add_subparsers(dest="service_command")

    service_list = service_subcommands.add_parser("list", help="List known services")
    add_json_output_flag(service_list)
    service_list.set_defaults(handler=run_service, needs_app=False)

    service_status = service_subcommands.add_parser(
        "status", help="Show service status"
    )
    service_status.add_argument(
        "service", nargs="?", default="", help="Optional service id"
    )
    add_json_output_flag(service_status)
    service_status.set_defaults(handler=run_service, needs_app=False)

    for action in ("start", "stop", "restart", "logs"):
        parser = service_subcommands.add_parser(
            action, help=f"{action.title()} a managed service"
        )
        parser.add_argument("service", help="Service id")
        parser.add_argument(
            "--sidecar",
            default="pinchtab",
            help="Sidecar name for sidecar lifecycle actions",
        )
        parser.add_argument(
            "--lines", type=int, default=200, help="Log tail line count"
        )
        parser.add_argument(
            "--follow",
            "-f",
            action="store_true",
            help="Follow log output when supported",
        )
        parser.add_argument(
            "--yes", action="store_true", help="Approve sidecar start when supported"
        )
        parser.add_argument(
            "--no-prompt", action="store_true", help="Disable interactive prompts"
        )
        parser.add_argument(
            "--kill", action="store_true", help="Force stop when supported"
        )
        add_json_output_flag(parser)
        parser.set_defaults(handler=run_service, needs_app=False)
