from __future__ import annotations

import argparse
import logging
import sys
from typing import Any

from openminion.base.config.core import resolve_default_agent_id
from openminion.base.config.env import resolve_environment_config
from openminion.cli.config import load_cli_config_from_args
from openminion.cli.parser.flags import add_json_output_flag
from openminion.cli.presentation.json_output import print_json_payload
from openminion.services.runtime.sidecars import (
    SidecarManager,
    default_sidecar_manager,
)
from openminion.modules.policy import (
    SecurityPolicyContext,
    SecurityPolicyEngine,
    ToolBudgetPolicy,
    default_internal_actor,
)


def run_sidecar(args: Any) -> int:
    action = str(getattr(args, "sidecar_command", "") or "").strip().lower()
    if not action:
        raise RuntimeError(
            "sidecar command is required (status/start/stop/restart/approve/deny/list)"
        )

    config = load_cli_config_from_args(args)
    logger = logging.getLogger("openminion.sidecars")

    if action == "pinchtab":
        return _run_pinchtab_binary_command(args, config, logger=logger)

    manager = _build_manager(args, config, logger=logger)

    if action == "list":
        payload = {"ok": True, "sidecars": manager.list()}
        _emit_sidecar_payload(payload, as_json=bool(getattr(args, "json", False)))
        return 0

    if action == "status":
        name = str(getattr(args, "name", "") or "").strip()
        payload = {"ok": True, "sidecars": _collect_statuses(manager, name=name)}
        _emit_sidecar_payload(payload, as_json=bool(getattr(args, "json", False)))
        return 0

    if action == "start":
        name = _require_name(args, manager=manager)
        if getattr(args, "yes", False):
            manager.approve(name)
        result = manager.ensure_started(
            name=name,
            interactive=bool(sys.stdin.isatty())
            and not bool(getattr(args, "no_prompt", False)),
        )
        payload = {"ok": True, "action": "start", "sidecar": name, "result": result}
        _emit_sidecar_payload(payload, as_json=bool(getattr(args, "json", False)))
        return 0

    if action == "stop":
        name = _require_name(args, manager=manager)
        result = manager.stop(name=name, kill=bool(getattr(args, "kill", False)))
        payload = {"ok": True, "action": "stop", "sidecar": name, "result": result}
        _emit_sidecar_payload(payload, as_json=bool(getattr(args, "json", False)))
        return 0

    if action == "restart":
        name = _require_name(args, manager=manager)
        stop_result = manager.stop(name=name, kill=bool(getattr(args, "kill", False)))
        start_result = manager.ensure_started(
            name=name,
            interactive=bool(sys.stdin.isatty())
            and not bool(getattr(args, "no_prompt", False)),
        )
        payload = {
            "ok": True,
            "action": "restart",
            "sidecar": name,
            "result": {"stop": stop_result, "start": start_result},
        }
        _emit_sidecar_payload(payload, as_json=bool(getattr(args, "json", False)))
        return 0

    if action == "approve":
        name = _require_name(args, manager=manager)
        consent = manager.approve(name)
        payload = {
            "ok": True,
            "action": "approve",
            "sidecar": name,
            "consent": consent.__dict__,
        }
        _emit_sidecar_payload(payload, as_json=bool(getattr(args, "json", False)))
        return 0

    if action == "deny":
        name = _require_name(args, manager=manager)
        consent = manager.deny(name)
        payload = {
            "ok": True,
            "action": "deny",
            "sidecar": name,
            "consent": consent.__dict__,
        }
        _emit_sidecar_payload(payload, as_json=bool(getattr(args, "json", False)))
        return 0

    raise RuntimeError(f"Unknown sidecar command: {action}")


def _build_manager(args: Any, config: Any, *, logger: logging.Logger) -> SidecarManager:
    runtime_env = getattr(getattr(config, "runtime", None), "env", None)
    config_path = str(getattr(args, "config", "") or "").strip() or None
    policy = SecurityPolicyEngine(
        tool_budget_policy=ToolBudgetPolicy(
            max_calls_per_run=config.security.tool_policy.max_calls_per_run,
            max_calls_per_tool=config.security.tool_policy.max_calls_per_tool,
            max_budget_cost_per_run=config.security.tool_policy.max_budget_cost_per_run,
        ),
        default_tool_required_scopes=frozenset(
            config.security.tool_policy.default_required_scopes
        ),
    )
    actor = default_internal_actor(
        agent_id=resolve_default_agent_id(config), include_admin=True
    )
    context = SecurityPolicyContext(channel="cli", target="sidecar")
    return default_sidecar_manager(
        config_path=config_path,
        runtime_env=runtime_env,
        policy=policy,
        actor=actor,
        context=context,
        logger=logger,
    )


def _run_pinchtab_binary_command(
    args: Any, config: Any, *, logger: logging.Logger
) -> int:
    from openminion.services.config import resolve_services_roots
    from openminion.tools.browser.providers.pinchtab.binary import (
        PINCHTAB_ALLOW_EXTERNAL_ENV,
        PINCHTAB_DOWNLOAD_URL_ENV,
        PINCHTAB_INSTALL_MODE_ENV,
        PINCHTAB_SHA256_ENV,
        PINCHTAB_VERSION_ENV,
        build_pinchtab_binary_resolver,
    )

    command = str(getattr(args, "pinchtab_command", "") or "").strip().lower()
    if command not in {"status", "install"}:
        raise RuntimeError("pinchtab command is required (status/install)")
    runtime_env = dict(getattr(getattr(config, "runtime", None), "env", None) or {})
    if getattr(args, "version", ""):
        runtime_env[PINCHTAB_VERSION_ENV] = str(getattr(args, "version"))
    if getattr(args, "sha256", ""):
        runtime_env[PINCHTAB_SHA256_ENV] = str(getattr(args, "sha256"))
    if getattr(args, "download_url", ""):
        runtime_env[PINCHTAB_DOWNLOAD_URL_ENV] = str(getattr(args, "download_url"))
    if bool(getattr(args, "allow_external", False)):
        runtime_env[PINCHTAB_ALLOW_EXTERNAL_ENV] = "1"
    config_path = str(getattr(args, "config", "") or "").strip() or None
    roots = resolve_services_roots(config_path=config_path, runtime_env=runtime_env)
    if command == "install":
        runtime_env[PINCHTAB_INSTALL_MODE_ENV] = "required"
    resolver = build_pinchtab_binary_resolver(
        data_root=roots.data_root,
        runtime_env=runtime_env,
        event_sink=lambda event, payload: logger.info(
            "sidecar event=%s payload=%s", event, payload
        ),
    )
    if command == "install":
        result = resolver.resolve(allow_download=True).as_dict()
        payload = {"ok": True, "action": "pinchtab-install", "binary": result}
    else:
        payload = {
            "ok": True,
            "action": "pinchtab-status",
            "binary": resolver.status().as_dict(),
        }
    _emit_sidecar_payload(payload, as_json=bool(getattr(args, "json", False)))
    return 0


def _collect_statuses(
    manager: SidecarManager, *, name: str | None
) -> list[dict[str, Any]]:
    registered = manager.list()
    if name and name not in registered:
        raise RuntimeError(f"Unknown sidecar: {name}")
    names = [name] if name else registered
    spec_by_name = {spec.name: spec for spec in manager.specs()}
    env_owner = resolve_environment_config()
    statuses: list[dict[str, Any]] = []
    for sidecar in names:
        status = manager.status(sidecar)
        consent = manager.consent(sidecar)
        status["consent"] = consent.__dict__ if consent else None
        spec = spec_by_name.get(sidecar)
        status["autostart_env"] = {
            "key": spec.autostart_env_key if spec else "",
            "value": env_owner.get(spec.autostart_env_key, "") if spec else "",
        }
        statuses.append(status)
    return statuses


def _emit_sidecar_payload(payload: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print_json_payload(payload)
        return
    action = str(payload.get("action", "") or "").strip()
    if action:
        result = payload.get("result", {})
        print(
            "sidecar "
            f"{action}: name={payload.get('sidecar', '-')} result={_sidecar_result_label(result)}"
        )
        return
    sidecars = payload.get("sidecars", [])
    if isinstance(sidecars, list) and sidecars and isinstance(sidecars[0], dict):
        print(f"sidecar status: count={len(sidecars)}")
        for item in sidecars:
            consent = item.get("consent") if isinstance(item, dict) else None
            approved = (
                bool(consent.get("approved", False))
                if isinstance(consent, dict)
                else False
            )
            print(
                f"- {item.get('sidecar', 'unknown')} "
                f"ok={bool(item.get('ok', False))} "
                f"pid_alive={bool(item.get('pid_alive', False))} "
                f"consent={'approved' if approved else 'not-approved'}"
            )
        return
    if isinstance(sidecars, list):
        print(f"sidecar list: {', '.join(str(item) for item in sidecars) or '(none)'}")
        return
    print("sidecar list: (none)")


def _sidecar_result_label(result: object) -> str:
    if not isinstance(result, dict):
        return str(result)
    if "started" in result:
        return "started" if result.get("started") else "not-started"
    if "stopped" in result:
        return "stopped" if result.get("stopped") else "not-stopped"
    return "ok"


def _require_name(args: Any, *, manager: SidecarManager | None = None) -> str:
    name = str(getattr(args, "name", "") or "").strip()
    if not name:
        raise RuntimeError("sidecar name is required")
    if manager is not None and name not in manager.list():
        raise RuntimeError(f"Unknown sidecar: {name}")
    return name


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    sidecar = subparsers.add_parser("sidecar", help="Sidecar lifecycle controls")
    add_json_output_flag(sidecar)
    sidecar_subcommands = sidecar.add_subparsers(dest="sidecar_command")

    sidecar_list = sidecar_subcommands.add_parser(
        "list", help="List registered sidecars"
    )
    add_json_output_flag(sidecar_list)
    sidecar_list.set_defaults(handler=run_sidecar, needs_app=False)

    sidecar_status = sidecar_subcommands.add_parser(
        "status", help="Show sidecar status"
    )
    sidecar_status.add_argument("name", nargs="?", default="", help="Sidecar name")
    add_json_output_flag(sidecar_status)
    sidecar_status.set_defaults(handler=run_sidecar, needs_app=False)

    sidecar_start = sidecar_subcommands.add_parser("start", help="Start a sidecar")
    sidecar_start.add_argument("name", help="Sidecar name")
    sidecar_start.add_argument(
        "--yes",
        action="store_true",
        help="Approve autostart before starting",
    )
    sidecar_start.add_argument(
        "--no-prompt",
        action="store_true",
        help="Disable interactive consent prompt",
    )
    add_json_output_flag(sidecar_start)
    sidecar_start.set_defaults(handler=run_sidecar, needs_app=False)

    sidecar_stop = sidecar_subcommands.add_parser("stop", help="Stop a sidecar")
    sidecar_stop.add_argument("name", help="Sidecar name")
    sidecar_stop.add_argument("--kill", action="store_true", help="Force kill")
    add_json_output_flag(sidecar_stop)
    sidecar_stop.set_defaults(handler=run_sidecar, needs_app=False)

    sidecar_restart = sidecar_subcommands.add_parser(
        "restart", help="Restart a sidecar"
    )
    sidecar_restart.add_argument("name", help="Sidecar name")
    sidecar_restart.add_argument("--kill", action="store_true", help="Force kill")
    sidecar_restart.add_argument(
        "--no-prompt",
        action="store_true",
        help="Disable interactive consent prompt",
    )
    add_json_output_flag(sidecar_restart)
    sidecar_restart.set_defaults(handler=run_sidecar, needs_app=False)

    sidecar_approve = sidecar_subcommands.add_parser(
        "approve", help="Persist consent for a sidecar"
    )
    sidecar_approve.add_argument("name", help="Sidecar name")
    add_json_output_flag(sidecar_approve)
    sidecar_approve.set_defaults(handler=run_sidecar, needs_app=False)

    sidecar_deny = sidecar_subcommands.add_parser(
        "deny", help="Revoke consent for a sidecar"
    )
    sidecar_deny.add_argument("name", help="Sidecar name")
    add_json_output_flag(sidecar_deny)
    sidecar_deny.set_defaults(handler=run_sidecar, needs_app=False)

    _register_pinchtab_binary_commands(sidecar_subcommands)


def _register_pinchtab_binary_commands(
    sidecar_subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    pinchtab = sidecar_subcommands.add_parser(
        "pinchtab", help="PinchTab binary install/status"
    )
    add_json_output_flag(pinchtab)
    pinchtab_subcommands = pinchtab.add_subparsers(dest="pinchtab_command")
    pinchtab_status = pinchtab_subcommands.add_parser(
        "status", help="Show PinchTab managed-binary status"
    )
    pinchtab_status.add_argument("--version", default="", help="PinchTab version")
    pinchtab_status.add_argument(
        "--allow-external",
        action="store_true",
        help="Allow explicitly approved external binary fallback",
    )
    add_json_output_flag(pinchtab_status)
    pinchtab_status.set_defaults(handler=run_sidecar, needs_app=False)

    pinchtab_install = pinchtab_subcommands.add_parser(
        "install", help="Install or verify the managed PinchTab binary"
    )
    pinchtab_install.add_argument(
        "--version", default="latest", help="PinchTab version"
    )
    pinchtab_install.add_argument("--sha256", default="", help="Expected sha256")
    pinchtab_install.add_argument(
        "--download-url",
        default="",
        help="Explicit release asset URL for installation",
    )
    pinchtab_install.add_argument(
        "--allow-external",
        action="store_true",
        help="Allow explicitly approved external binary fallback",
    )
    add_json_output_flag(pinchtab_install)
    pinchtab_install.set_defaults(handler=run_sidecar, needs_app=False)
