from __future__ import annotations

import logging
import shlex
import sys
from typing import Any

from openminion.base.config.core import resolve_default_agent_id
from openminion.cli.presentation import styles
from openminion.cli.presentation.json_output import print_json_payload
from openminion.cli.config import resolve_cli_tool_provider_specs_and_dispatch_map
from openminion.cli.transport.daemon_client import daemon_request
from openminion.services.lifecycle.sidecars import default_sidecar_manager
from openminion.services.security.policy import (
    SecurityPolicyContext,
    SecurityPolicyEngine,
    ToolBudgetPolicy,
    default_internal_actor,
)

from ..runtime import ChatRuntimeState, ensure_inproc_runtime
from ..ui import print_tools_from_payload, set_quiet_log_level


def _print_tools(args: Any, runtime_state: ChatRuntimeState, *, quiet: bool) -> None:
    verbose = getattr(args, "verbose", False) or getattr(args, "tools_verbose", False)

    if runtime_state.endpoint is not None:
        try:
            status, payload = daemon_request(
                endpoint=runtime_state.endpoint,
                method="GET",
                path="/v1/tools",
                timeout_s=5,
            )
            if status < 400 and payload.get("ok"):
                print_tools_from_payload(payload.get("tools", []), verbose=verbose)
                return
        except RuntimeError:
            pass

    owns_runtime = runtime_state.inproc_runtime is None
    active_runtime = ensure_inproc_runtime(runtime_state, args.config)
    if quiet:
        set_quiet_log_level()
    try:
        specs, dispatch_map = resolve_cli_tool_provider_specs_and_dispatch_map(
            active_runtime.tools
        )
        tool_list = []
        for spec in specs:
            mapping = (
                dispatch_map.get(spec.name, {})
                if isinstance(dispatch_map, dict)
                else {}
            )
            tool_list.append(
                {
                    "name": spec.name,
                    "description": spec.description,
                    "source": getattr(spec, "source", "core"),
                    "enabled": True,
                    "runtime_binding_id": str(mapping.get("runtime_binding_id", "")),
                    "runtime_tool_name": str(mapping.get("runtime_tool_name", "")),
                }
            )
        print_tools_from_payload(tool_list, verbose=verbose)
    finally:
        if owns_runtime and runtime_state.inproc_runtime is not None:
            runtime_state.inproc_runtime.close()
            runtime_state.inproc_runtime = None


def _handle_sidecar_command(*, line: str, config: Any, args: Any) -> None:
    try:
        tokens = shlex.split(line)
    except ValueError as exc:
        print(
            styles.style(styles.StyleToken.ERROR, f"sidecar command parse error: {exc}")
        )
        _print_sidecar_help()
        return

    if len(tokens) <= 1 or tokens[1] in {"help", "--help", "-h", "?"}:
        _print_sidecar_help()
        return

    action = tokens[1].lower()
    name = tokens[2] if len(tokens) > 2 else ""
    runtime_env = getattr(getattr(config, "runtime", None), "env", None)
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
    context = SecurityPolicyContext(channel="chat", target="sidecar")
    manager = default_sidecar_manager(
        config_path=str(getattr(args, "config", "") or "") or None,
        runtime_env=runtime_env,
        policy=policy,
        actor=actor,
        context=context,
        logger=logging.getLogger("openminion.sidecars"),
    )

    if action == "list":
        print_json_payload({"sidecars": manager.list()})
        return
    if action == "status":
        if name and name not in manager.list():
            print(styles.style(styles.StyleToken.ERROR, f"unknown sidecar: {name}"))
            return
        names = [name] if name else manager.list()
        statuses = []
        for sidecar in names:
            status = manager.status(sidecar)
            consent = manager.consent(sidecar)
            status["consent"] = consent.__dict__ if consent else None
            statuses.append(status)
        print_json_payload({"sidecars": statuses})
        return
    if not name:
        print(styles.style(styles.StyleToken.ERROR, "usage: /sidecar <action> <name>"))
        _print_sidecar_help()
        return
    if name not in manager.list():
        print(styles.style(styles.StyleToken.ERROR, f"unknown sidecar: {name}"))
        return
    if action == "start":
        result = manager.ensure_started(
            name=name,
            interactive=bool(sys.stdin.isatty()),
        )
        print_json_payload(
            {"action": "start", "sidecar": name, "result": result},
            sort_keys=False,
        )
        return
    if action == "stop":
        result = manager.stop(name=name, kill=False)
        print_json_payload(
            {"action": "stop", "sidecar": name, "result": result},
            sort_keys=False,
        )
        return
    if action == "approve":
        consent = manager.approve(name)
        print_json_payload(
            {"action": "approve", "sidecar": name, "consent": consent.__dict__},
            sort_keys=False,
        )
        return
    if action == "deny":
        consent = manager.deny(name)
        print_json_payload(
            {"action": "deny", "sidecar": name, "consent": consent.__dict__},
            sort_keys=False,
        )
        return

    print(styles.style(styles.StyleToken.ERROR, f"unknown /sidecar action: {action}"))
    _print_sidecar_help()


def _print_sidecar_help() -> None:
    print(
        "\n".join(
            [
                "Sidecar commands:",
                "  /sidecar list",
                "  /sidecar status [name]",
                "  /sidecar start <name>",
                "  /sidecar stop <name>",
                "  /sidecar approve <name>",
                "  /sidecar deny <name>",
            ]
        )
    )
