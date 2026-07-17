from __future__ import annotations

import argparse
import logging
import sys

from openminion.base.config.core import resolve_default_agent_id
from openminion.base.config.env import resolve_environment_config
from openminion.cli.bootstrap.loader import load_config
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


def run_sidecar(args) -> int:
    action = str(getattr(args, "sidecar_command", "") or "").strip().lower()
    if not action:
        raise RuntimeError(
            "sidecar command is required (status/start/stop/approve/deny/list)"
        )

    config = load_config(args.config)
    logger = logging.getLogger("openminion.sidecars")
    manager = _build_manager(args, config, logger=logger)

    if action == "list":
        payload = {"ok": True, "sidecars": manager.list()}
        print_json_payload(payload)
        return 0

    if action == "status":
        name = str(getattr(args, "name", "") or "").strip()
        payload = {"ok": True, "sidecars": _collect_statuses(manager, name=name)}
        print_json_payload(payload)
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
        print_json_payload(payload)
        return 0

    if action == "stop":
        name = _require_name(args, manager=manager)
        result = manager.stop(name=name, kill=bool(getattr(args, "kill", False)))
        payload = {"ok": True, "action": "stop", "sidecar": name, "result": result}
        print_json_payload(payload)
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
        print_json_payload(payload)
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
        print_json_payload(payload)
        return 0

    raise RuntimeError(f"Unknown sidecar command: {action}")


def _build_manager(args, config, *, logger: logging.Logger) -> SidecarManager:
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
    manager = default_sidecar_manager(
        config_path=config_path,
        runtime_env=runtime_env,
        policy=policy,
        actor=actor,
        context=context,
        logger=logger,
    )
    return manager


def _collect_statuses(manager: SidecarManager, *, name: str | None) -> list[dict]:
    names = [name] if name else manager.list()
    if name and name not in manager.list():
        raise RuntimeError(f"Unknown sidecar: {name}")
    spec_by_name = {spec.name: spec for spec in manager.specs()}
    env_owner = resolve_environment_config()
    statuses: list[dict] = []
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


def _require_name(args, *, manager: SidecarManager | None = None) -> str:
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
    sidecar_list.set_defaults(handler=run_sidecar, needs_app=False)

    sidecar_status = sidecar_subcommands.add_parser(
        "status", help="Show sidecar status"
    )
    sidecar_status.add_argument("name", nargs="?", default="", help="Sidecar name")
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
    sidecar_start.set_defaults(handler=run_sidecar, needs_app=False)

    sidecar_stop = sidecar_subcommands.add_parser("stop", help="Stop a sidecar")
    sidecar_stop.add_argument("name", help="Sidecar name")
    sidecar_stop.add_argument("--kill", action="store_true", help="Force kill")
    sidecar_stop.set_defaults(handler=run_sidecar, needs_app=False)

    sidecar_approve = sidecar_subcommands.add_parser(
        "approve", help="Persist consent for a sidecar"
    )
    sidecar_approve.add_argument("name", help="Sidecar name")
    sidecar_approve.set_defaults(handler=run_sidecar, needs_app=False)

    sidecar_deny = sidecar_subcommands.add_parser(
        "deny", help="Revoke consent for a sidecar"
    )
    sidecar_deny.add_argument("name", help="Sidecar name")
    sidecar_deny.set_defaults(handler=run_sidecar, needs_app=False)
