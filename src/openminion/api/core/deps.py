"""Shared dependency helpers for API routes and handlers."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Mapping, Optional

from openminion.base.config.env import resolve_environment_config
from openminion.base.config import ConfigManager
from openminion.api.config import (
    close_api_runtime_if_owned,
    resolve_api_runtime,
)
from openminion.api.constants import API_METRICS_TOKEN_ENV, API_METRICS_TOKEN_HEADER
from openminion.api.runtime import APIRuntime
from openminion.modules.tool.refs import tool_result_artifact_refs
from openminion.services.bootstrap.config import bootstrap_config_manager
from openminion.services.diagnostics.debug import (
    DebugRegistry,
    DebugStatus,
    ModuleDebugPayload,
    WiringSource,
    is_debug_surface_enabled,
)
from openminion.services.tool.exposure import get_visible_tool_specs_and_dispatch_map

_DEFAULT_API_CONFIG_HINT = "~/.openminion/config.json"


def resolve_api_config_display_path(config_path: Optional[str | Path]) -> str:
    if config_path is None:
        return ""
    try:
        return str(Path(config_path).expanduser().resolve(strict=False))
    except Exception:
        return str(config_path)


def resolve_api_config_hint(config_path: Optional[str | Path]) -> str:
    candidate = str(config_path or "").strip()
    return candidate or _DEFAULT_API_CONFIG_HINT


def resolve_api_tool_provider_specs_and_dispatch_map(
    runtime_tools: Any,
) -> tuple[list[Any], dict[str, Any]]:
    return get_visible_tool_specs_and_dispatch_map(runtime_tools)


def resolve_runtime_manager(
    *,
    config_path: Optional[str],
    runtime: Optional[APIRuntime],
) -> tuple[object, APIRuntime, bool]:
    active_runtime, own_runtime = resolve_api_runtime(
        config_path=config_path,
        runtime=runtime,
    )
    manager = getattr(active_runtime, "runtime_manager", None)
    if manager is None:
        close_api_runtime_if_owned(active_runtime, own_runtime=own_runtime)
        raise RuntimeError("runtime manager is unavailable")
    return manager, active_runtime, own_runtime


def configured_agent_ids(runtime: APIRuntime) -> list[str]:
    if hasattr(runtime, "list_registered_agents"):
        listed = getattr(runtime, "list_registered_agents")
        if callable(listed):
            try:
                resolved = listed()
                if isinstance(resolved, list):
                    return sorted(
                        str(item).strip() for item in resolved if str(item).strip()
                    )
            except Exception:
                pass
    configured = {str(item).strip() for item in runtime.config.agents.keys()}
    return sorted(item for item in configured if item)


def v1_tool_specs(runtime: APIRuntime) -> list[dict[str, Any]]:
    return runtime.tool_inventory_report()


def v1_tool_schema(runtime: APIRuntime, *, tool_name: str) -> Optional[dict[str, Any]]:
    normalized = str(tool_name or "").strip()
    if not normalized:
        return None
    return runtime.tool_schema_report(tool_name=normalized)


def v1_capability_report(
    runtime: APIRuntime,
    *,
    agent_id: str | None = None,
) -> dict[str, Any]:
    return runtime.capability_report(agent_id=agent_id)


def v1_runtime_posture(
    runtime: APIRuntime,
    *,
    agent_id: str | None = None,
) -> dict[str, Any]:
    return runtime.runtime_posture(agent_id=agent_id)


def v1_runtime_self_model(
    runtime: APIRuntime,
    *,
    agent_id: str | None = None,
) -> dict[str, Any]:
    return runtime.runtime_self_model(agent_id=agent_id)


def v1_tool_result_artifact_refs(
    *, trace_id: str, session_id: str, result: Any
) -> list[dict[str, str]]:
    return tool_result_artifact_refs(
        trace_id=trace_id,
        session_id=session_id,
        result=result,
    )


def _probe_runtime_subsystem(
    runtime: APIRuntime,
    name: str,
    candidates: tuple[str, ...],
) -> dict[str, Any]:
    target = None
    for attr in candidates:
        value = getattr(runtime, attr, None)
        if value is not None:
            target = value
            break
    if target is None:
        return {"status": "unknown", "available": False}

    for method_name in ("healthcheck", "health", "status"):
        method = getattr(target, method_name, None)
        if not callable(method):
            continue
        try:
            result = method()
        except Exception as exc:
            return {
                "status": "unavailable",
                "available": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
        if isinstance(result, Mapping):
            ok = bool(result.get("ok", result.get("available", True)))
            payload = dict(result)
            payload.setdefault("available", ok)
            payload.setdefault("status", "ok" if ok else "unavailable")
            return payload
        ok = bool(result)
        return {"status": "ok" if ok else "unavailable", "available": ok}

    return {
        "status": "ok",
        "available": True,
        "detail": f"{name} object present; no healthcheck method",
    }


def _runtime_subsystem_health(runtime: APIRuntime) -> dict[str, Any]:
    return {
        "storage": _probe_runtime_subsystem(
            runtime,
            "storage",
            ("record_store", "storage", "runtime_storage"),
        ),
        "memory": _probe_runtime_subsystem(
            runtime,
            "memory",
            ("memory", "memory_api", "memoryctl"),
        ),
        "provider": _probe_runtime_subsystem(
            runtime,
            "provider",
            ("provider", "llm", "llm_api", "llmctl"),
        ),
    }


def _register_debug_provider(
    registry: DebugRegistry,
    *,
    module_name: str,
    probe_fn: Callable[[], ModuleDebugPayload],
) -> None:
    registry.register(
        type(
            "DebugProvider",
            (),
            {
                "module_name": module_name,
                "probe_fn": probe_fn,
                "wiring_check_fn": None,
            },
        )()
    )


def register_api_debug_providers(
    registry: DebugRegistry, runtime: Optional[APIRuntime]
) -> None:
    def make_probe(
        module: str,
        status: DebugStatus,
        wiring: WiringSource,
        details: dict[str, Any] | None = None,
    ) -> Callable[[], ModuleDebugPayload]:
        def probe() -> ModuleDebugPayload:
            return ModuleDebugPayload(
                module=module,
                status=status,
                mode="daemon",
                wiring_source=wiring,
                details=details or {},
            )

        return probe

    if runtime is not None:
        tool_count = len(
            resolve_api_tool_provider_specs_and_dispatch_map(runtime.tools)[0]
        )
        plugin_names = runtime.plugins.names()
        provider = getattr(runtime, "provider", None)
        provider_name = str(getattr(provider, "name", "") or "unknown")

        _register_debug_provider(
            registry,
            module_name="openminion",
            probe_fn=make_probe(
                "openminion",
                DebugStatus.OK,
                WiringSource.REAL,
                {"provider": provider_name},
            ),
        )
        _register_debug_provider(
            registry,
            module_name="openminion-tool",
            probe_fn=make_probe(
                "openminion-tool",
                DebugStatus.OK,
                WiringSource.REAL,
                {"tool_count": tool_count},
            ),
        )
        _register_debug_provider(
            registry,
            module_name="openminion-plugins",
            probe_fn=make_probe(
                "openminion-plugins",
                DebugStatus.OK,
                WiringSource.REAL if plugin_names else WiringSource.STUB,
                {"loaded_plugins": plugin_names},
            ),
        )
    else:
        _register_debug_provider(
            registry,
            module_name="openminion",
            probe_fn=make_probe(
                "openminion",
                DebugStatus.WARN,
                WiringSource.UNKNOWN,
                {"note": "runtime not available"},
            ),
        )


def v1_daemon_health(
    runtime: Optional[APIRuntime],
    *,
    config_path: Optional[str] = None,
) -> dict[str, object]:
    runtime_config_path = (
        getattr(runtime, "config_path", None) if runtime is not None else None
    )
    active_config_path = runtime_config_path or config_path
    resolved_config_path = resolve_api_config_display_path(active_config_path)
    if runtime is None:
        return {
            "available": False,
            "config_path": resolved_config_path,
            "subsystems": {},
        }
    manager = getattr(runtime, "runtime_manager", None)
    if manager is None:
        return {
            "available": False,
            "config_path": resolved_config_path,
            "subsystems": _runtime_subsystem_health(runtime),
        }
    list_agents = getattr(manager, "list_agents", None)
    if not callable(list_agents):
        return {
            "available": True,
            "agents_hot": 0,
            "config_path": resolved_config_path,
            "subsystems": _runtime_subsystem_health(runtime),
        }
    try:
        statuses = list_agents()
    except Exception:
        statuses = []
    return {
        "available": True,
        "agents_hot": len(statuses),
        "config_path": resolved_config_path,
        "subsystems": _runtime_subsystem_health(runtime),
    }


def build_degraded_recovery_hint(
    *,
    config_path: Optional[str],
    health_payload: dict[str, Any],
    bootstrap_error: str,
) -> dict[str, Any]:
    provider_name = str(health_payload.get("provider", "")).strip().lower()
    config_hint = resolve_api_config_hint(config_path)
    actions = [
        f"Run `openminion --config {config_hint} doctor --json` to inspect bootstrap checks.",
        f"Run `openminion --config {config_hint} config show` to verify effective provider settings.",
        "Fix the reported bootstrap issue and restart `openminion api run`.",
    ]

    if "API key is missing" in bootstrap_error:
        env_var = provider_default_env(provider_name)
        if env_var:
            actions.insert(
                1,
                (
                    f"Set provider credentials (`providers.{provider_name}.api_key`) or export "
                    f"`{env_var}`."
                ),
            )

    return {
        "configured_provider": provider_name,
        "summary": "API runtime is degraded because startup bootstrap failed.",
        "recommended_actions": actions,
    }


def is_debug_api_enabled(
    *, config_path: Optional[str], runtime: Optional[APIRuntime]
) -> bool:
    try:
        if runtime is not None:
            config = runtime.config
        else:
            manager = ConfigManager.load(config_path)
            bootstrap_config_manager(manager)
            config = manager.base_config
    except Exception:
        return False
    return is_debug_surface_enabled(config, surface="api")


def provider_default_env(provider_name: str) -> str:
    mapping = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "claude": "ANTHROPIC_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "ollama": "OLLAMA_API_KEY",
        "cortensor": "CORTENSOR_API_KEY",
    }
    return mapping.get(provider_name, "")


def authorize_metrics_request(
    request_headers: Optional[Mapping[str, str]],
) -> Optional[str]:
    required_token = resolve_environment_config().get(API_METRICS_TOKEN_ENV, "").strip()
    if not required_token:
        return None

    presented_token = ""
    if request_headers is not None:
        candidate = request_headers.get(API_METRICS_TOKEN_HEADER)
        if isinstance(candidate, str):
            presented_token = candidate.strip()

    if presented_token and presented_token == required_token:
        return None

    return "Metrics endpoint requires a valid operator token."
