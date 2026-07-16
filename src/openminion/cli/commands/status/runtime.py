from __future__ import annotations

import logging
from typing import Any

from openminion.api.runtime import APIRuntime
from openminion.base.config import build_capability_runtime_diagnostics
from openminion.base.config.core import resolve_default_agent_id
from openminion.cli.commands.daemon import ensure_daemon_running
from openminion.cli.presentation.json_output import print_json_payload
from openminion.cli.transport.daemon_client import daemon_request
from openminion.services.bootstrap.onboarding import (
    OnboardingInspectionRequest,
    OnboardingState,
    OnboardingStatusService,
)
from openminion.services.runtime.bootstrap import enforce_plugin_activation_policy
from openminion.services.runtime.lifecycle import LifecycleService
from openminion.services.security.policy import SecurityPolicyEngine, ToolBudgetPolicy


def run_tools_status(args, *, config) -> int:
    source, payload = _load_runtime_surface_payload(
        args=args,
        config=config,
        path="/v1/runtime/capabilities",
        inproc_call=lambda: _build_inproc_capabilities_payload(args.config),
    )

    capability_payload = dict(payload.get("capabilities", {}) or {})
    tools_raw = capability_payload.get("tools", {}).get("inventory", [])
    tools: list[dict[str, Any]] = [item for item in tools_raw if isinstance(item, dict)]
    available_count = sum(
        1
        for item in tools
        if bool(item.get("enabled", True))
        and item.get("policy_allowed", True) is not False
    )
    blocked_count = sum(
        1 for item in tools if item.get("policy_allowed", True) is False
    )
    disabled_count = sum(1 for item in tools if not bool(item.get("enabled", True)))
    output = {
        "ok": bool(payload.get("ok", False)),
        "source": source,
        "tool_count": len(tools),
        "available_count": available_count,
        "blocked_count": blocked_count,
        "disabled_count": disabled_count,
        "tools": tools,
    }
    if getattr(args, "json", False):
        print_json_payload(output)
    else:
        print(
            "status tools: "
            f"ok={output['ok']} source={source} total={output['tool_count']} "
            f"available={available_count} blocked={blocked_count} disabled={disabled_count}"
        )
    return 0 if output["ok"] else 1


def run_capabilities_status(args, *, config) -> int:
    source, payload = _load_runtime_surface_payload(
        args=args,
        config=config,
        path="/v1/runtime/capabilities",
        inproc_call=lambda: _build_inproc_capabilities_payload(args.config),
    )
    capabilities = dict(payload.get("capabilities", {}) or {})
    providers = dict(capabilities.get("providers", {}) or {})
    modes = dict(capabilities.get("modes", {}) or {})
    plugins = dict(capabilities.get("plugins", {}) or {})
    tools = dict(capabilities.get("tools", {}) or {})
    provider_items = [
        item for item in providers.get("items", []) if isinstance(item, dict)
    ]
    mode_items = [item for item in modes.get("items", []) if isinstance(item, dict)]
    plugin_items = [item for item in plugins.get("items", []) if isinstance(item, dict)]
    family_items = [
        item for item in tools.get("families", []) if isinstance(item, dict)
    ]
    inventory_items = [
        item for item in tools.get("inventory", []) if isinstance(item, dict)
    ]
    output = {
        "ok": bool(payload.get("ok", False)),
        "source": source,
        "capabilities": capabilities,
        "summary": {
            "providers_enabled": sum(
                1 for item in provider_items if bool(item.get("enabled", False))
            ),
            "providers_blocked": sum(
                1 for item in provider_items if not bool(item.get("enabled", False))
            ),
            "modes_enabled": sum(
                1 for item in mode_items if bool(item.get("enabled", False))
            ),
            "modes_blocked": sum(
                1
                for item in mode_items
                if str(item.get("blocked_reason", "") or "").strip()
            ),
            "plugins_enabled": sum(
                1 for item in plugin_items if bool(item.get("enabled", False))
            ),
            "plugins_blocked": sum(
                1
                for item in plugin_items
                if str(item.get("blocked_reason", "") or "").strip()
            ),
            "tool_families_configured": sum(
                1 for item in family_items if bool(item.get("configured", False))
            ),
            "visible_tools": len(inventory_items),
        },
    }
    if getattr(args, "json", False):
        print_json_payload(output)
        return 0 if output["ok"] else 1

    selected_provider = str(providers.get("selected", "") or "").strip() or "n/a"
    print(
        "status capabilities: "
        f"source={source} provider={selected_provider} "
        f"providers_enabled={output['summary']['providers_enabled']} "
        f"providers_blocked={output['summary']['providers_blocked']} "
        f"modes_enabled={output['summary']['modes_enabled']} "
        f"plugins_enabled={output['summary']['plugins_enabled']} "
        f"visible_tools={output['summary']['visible_tools']}"
    )
    return 0 if output["ok"] else 1


def run_runtime_status(args, *, config) -> int:
    source, payload = _load_runtime_surface_payload(
        args=args,
        config=config,
        path="/v1/runtime/posture",
        inproc_call=lambda: _build_inproc_runtime_posture_payload(args.config),
    )
    runtime_posture = dict(payload.get("runtime", {}) or {})
    output = {
        "ok": bool(payload.get("ok", False)),
        "source": source,
        "runtime": runtime_posture,
    }
    if getattr(args, "json", False):
        print_json_payload(output)
        return 0 if output["ok"] else 1

    print(
        "status runtime: "
        f"source={source} mode={runtime_posture.get('runtime_mode', 'unknown')} "
        f"bridge_active={runtime_posture.get('brain_bridge_active', False)} "
        f"fallback_reason={runtime_posture.get('fallback_reason', '') or '-'}"
    )
    return 0 if output["ok"] else 1


def _build_onboarding_capabilities(
    status,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    configured_now: list[dict[str, str]] = []
    available_later: list[dict[str, str]] = []

    if status.state == OnboardingState.EXPLICIT_DEMO:
        configured_now.append(
            {
                "id": "demo_mode",
                "label": "Explicit demo chat",
                "reason": "Configured now via demo mode.",
            }
        )
        available_later.extend(
            [
                {
                    "id": "cloud_setup",
                    "label": "Cloud provider chat",
                    "reason": "Run `openminion setup` to configure a real cloud provider.",
                },
                {
                    "id": "ollama_local",
                    "label": "Local Ollama chat",
                    "reason": "Run `openminion setup` to configure Ollama locally.",
                },
            ]
        )
        return configured_now, available_later

    if status.can_continue:
        configured_now.append(
            {
                "id": "provider_chat",
                "label": f"Provider-backed chat ({status.provider_name or 'configured'})",
                "reason": "Provider, config, and storage are ready now.",
            }
        )
    else:
        available_later.append(
            {
                "id": "provider_chat",
                "label": "Provider-backed chat",
                "reason": status.reason,
            }
        )

    if status.provider_name != "ollama":
        available_later.append(
            {
                "id": "ollama_local",
                "label": "Local Ollama chat",
                "reason": "Available later via `openminion setup`.",
            }
        )
    if status.state != OnboardingState.EXPLICIT_DEMO:
        available_later.append(
            {
                "id": "demo_mode",
                "label": "Explicit demo mode",
                "reason": "Available later via bare `openminion` or `openminion setup`.",
            }
        )
    return configured_now, available_later


def run_onboarding_status(args) -> int:
    from openminion.base.config import resolve_config_path
    from openminion.cli.config import resolve_cli_roots

    roots = resolve_cli_roots(
        config_path=getattr(args, "config", None),
        home_root=getattr(args, "home_root", None),
        data_root=getattr(args, "data_root", None),
    )
    config_path = resolve_config_path(
        getattr(args, "config", None),
        home_root=roots.home_root,
    )
    status = OnboardingStatusService().inspect(
        OnboardingInspectionRequest(
            config_path=config_path,
            home_root=roots.home_root,
            data_root=roots.data_root,
            config_arg=getattr(args, "config", None),
            agent_id=str(getattr(args, "agent_id", "") or "").strip() or None,
            has_tty=False,
            env=roots.env,
        )
    )
    configured_now, available_later = _build_onboarding_capabilities(status)
    payload = {
        "ok": True,
        "state": status.state.value,
        "action": status.action.value,
        "track": status.track.value,
        "reason": status.reason,
        "provider_name": status.provider_name,
        "config_exists": status.config_exists,
        "credentials_ready": status.credentials_ready,
        "configured_now": configured_now,
        "available_later": available_later,
    }
    if getattr(args, "json", False):
        print_json_payload(payload)
    else:
        print(
            "status onboarding: "
            f"state={payload['state']} action={payload['action']} provider={payload['provider_name'] or 'n/a'}"
        )
        print(f"reason: {payload['reason']}")
        for item in configured_now:
            print(f"- configured_now {item['id']}: {item['reason']}")
        for item in available_later:
            print(f"- available_later {item['id']}: {item['reason']}")
    return 0


def _load_runtime_surface_payload(
    *,
    args,
    config,
    path: str,
    inproc_call,
) -> tuple[str, dict[str, Any]]:
    payload: dict[str, Any] | None = None
    source = "inproc"
    auto_start = bool(getattr(config.runtime, "daemon_auto_start", False))
    try:
        endpoint = ensure_daemon_running(args.config, auto_start=auto_start)
        status_code, daemon_payload = daemon_request(
            endpoint=endpoint,
            method="GET",
            path=path,
            timeout_s=10,
        )
        if status_code < 400 and isinstance(daemon_payload, dict):
            payload = daemon_payload
            source = "daemon"
    except RuntimeError:
        payload = None

    if payload is None:
        payload = inproc_call()
        source = "inproc"
    return source, payload


def _build_inproc_capabilities_payload(config_path: str | None) -> dict[str, Any]:
    runtime = APIRuntime.from_config_path(config_path)
    try:
        return {"ok": True, "capabilities": runtime.capability_report()}
    finally:
        runtime.close()


def _build_inproc_runtime_posture_payload(config_path: str | None) -> dict[str, Any]:
    runtime = APIRuntime.from_config_path(config_path)
    try:
        return {"ok": True, "runtime": runtime.runtime_posture()}
    finally:
        runtime.close()


def run_extensions_status(args, *, config) -> int:
    logger = logging.getLogger("openminion.status.extensions")
    security_policy = SecurityPolicyEngine(
        tool_budget_policy=ToolBudgetPolicy(
            max_calls_per_run=config.security.tool_policy.max_calls_per_run,
            max_calls_per_tool=config.security.tool_policy.max_calls_per_tool,
            max_budget_cost_per_run=config.security.tool_policy.max_budget_cost_per_run,
        ),
        default_tool_required_scopes=frozenset(
            config.security.tool_policy.default_required_scopes
        ),
    )
    manager = LifecycleService.from_config(
        config,
        config_path=str(getattr(args, "config", "") or "") or None,
        logger=logger,
    )
    runtime = manager.build(
        security_policy=security_policy,
        on_before_activate=lambda manifest: enforce_plugin_activation_policy(
            security_policy=security_policy,
            agent_id=resolve_default_agent_id(config),
            manifest=manifest,
        ),
        load_tool_plugins=True,
    )
    payload = manager.status_payload(runtime)
    payload["capability_layering"] = build_capability_runtime_diagnostics(
        config,
        agent_id=resolve_default_agent_id(config),
    )
    if getattr(args, "json", False):
        print_json_payload(payload)
        return 0

    plugin_count = len(payload.get("plugins", []))
    tool_count = len(payload.get("tool_plugins", []))
    provider_count = len(payload.get("providers", []))
    sidecar_count = len(payload.get("sidecars", []))
    errors = payload.get("errors", []) or []
    print(
        "status extensions: "
        f"plugins={plugin_count} tool_plugins={tool_count} "
        f"providers={provider_count} sidecars={sidecar_count}"
    )
    if errors:
        print(f"- discovery errors: {len(errors)}")
        for error in errors:
            kind = error.get("kind", "error")
            detail = error.get("error", "")
            print(f"  - {kind}: {detail}")
    return 0
