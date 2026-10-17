from __future__ import annotations
import shlex
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from openminion.base.config import resolve_runtime_profile
from openminion.modules.tool import build_runtime_tool_routing_metadata
from openminion.modules.tool.base import ToolExecutionContext
from openminion.services.runtime.sidecars import default_sidecar_manager
from openminion.tools.browser import (
    BrowserProviderContext,
    BrowserRouter,
    default_browser_tool,
    provider_registry,
)
from openminion.tools.browser.constants import (
    BROWSER_PROVIDER_PINCHTAB,
    BROWSER_PROVIDER_PLAYWRIGHT,
    OPENMINION_BROWSER_DEFAULT_PROVIDER_ENV,
    OPENMINION_BROWSER_PLAYWRIGHT_LOCALE_ENV,
    OPENMINION_BROWSER_PLAYWRIGHT_TIMEZONE_ENV,
)
from openminion.tools.browser.providers.playwright import PlaywrightProvider
from openminion.tools.config import resolve_tool_env


def browser_command_payload(
    args: str,
    *,
    working_dir: str | None = None,
    runtime_env: Mapping[str, object] | None = None,
    browser_config: Any | None = None,
    config_path: str | None = None,
    session_id: str = "cli-browser",
    agent_id: str = "",
) -> dict[str, Any]:
    tokens = shlex.split(str(args or ""))
    action = tokens[0].lower() if tokens else "status"
    options = _parse_options(tokens[1:])
    if action == "status":
        return _browser_status_payload(
            runtime_env=runtime_env,
            browser_config=browser_config,
            config_path=config_path,
        )
    if action == "tabs":
        return _execute_browser_tool(
            {"op": "tab.list", **_provider_args(options)},
            working_dir,
            runtime_env=runtime_env,
            browser_config=browser_config,
            session_id=session_id,
            agent_id=agent_id,
        )
    if action == "navigate":
        url = options.get("url") or (tokens[1] if len(tokens) > 1 else "")
        if not url:
            return _error_payload("url is required for browser navigate")
        payload = {"op": "tab.navigate", "url": url, **_provider_args(options)}
        if options.get("tab"):
            payload["tab_id"] = options["tab"]
        return _execute_browser_tool(
            payload,
            working_dir,
            runtime_env=runtime_env,
            browser_config=browser_config,
            session_id=session_id,
            agent_id=agent_id,
        )
    if action == "stop":
        if _is_truthy(options.get("sidecar", "1")):
            result = _default_browser_sidecar_manager(
                config_path=config_path,
                runtime_env=runtime_env,
            ).stop(
                name="pinchtab",
                kill=_is_truthy(options.get("kill", "0")),
            )
            return {
                "ok": True,
                "action": "stop",
                "sidecar": "pinchtab",
                "result": result,
            }
        instance_id = options.get("instance") or options.get("instance_id")
        if not instance_id:
            return _error_payload("instance=<id> is required when sidecar=0")
        return _execute_browser_tool(
            {
                "op": "instance.kill",
                "instance_id": instance_id,
                **_provider_args(options),
            },
            working_dir,
            runtime_env=runtime_env,
            browser_config=browser_config,
            session_id=session_id,
            agent_id=agent_id,
        )
    return _error_payload("usage: /browser [status|tabs|navigate|stop]")


def render_browser_command(
    args: str,
    *,
    working_dir: str | None = None,
    payload: dict[str, Any] | None = None,
    runtime: Any | None = None,
) -> str:
    if payload is None:
        payload = browser_command_payload(
            args,
            working_dir=working_dir,
            **_runtime_browser_context(runtime),
        )
    if not payload.get("ok"):
        return f"Browser: error: {payload.get('error', 'unknown error')}"
    action = str(payload.get("action") or "").strip()
    if action == "status":
        providers = ", ".join(payload.get("providers", [])) or "(none)"
        selected = str(payload.get("selected_provider") or "(none)")
        readiness = "ready" if payload.get("selected_ready") else "not ready"
        context = payload.get("context", {})
        context_label = ""
        if isinstance(context, dict) and context:
            context_label = (
                f" locale={context.get('locale', '')}"
                f" timezone={context.get('timezone_id', '')}"
            )
        repair = str(payload.get("repair") or "").strip()
        repair_label = f"\nRepair: {repair}" if repair else ""
        return (
            f"Browser: selected={selected} status={readiness}{context_label}\n"
            f"Providers: {providers}{repair_label}"
        )
    if action == "stop":
        result = payload.get("result", {})
        stopped = bool(result.get("stopped")) if isinstance(result, dict) else False
        return f"Browser: pinchtab sidecar stop requested stopped={stopped}"
    data = payload.get("data", {})
    if action == "tabs":
        tabs = data.get("tabs", []) if isinstance(data, dict) else []
        active_tab_id = str(payload.get("active_tab_id") or "").strip()
        rows = [
            _format_tab_row(tab, active_tab_id=active_tab_id)
            for tab in tabs
            if isinstance(tab, dict)
        ]
        return "Browser tabs:\n" + ("\n".join(rows) or "(none)")
    if action == "navigate":
        tab = data.get("tab", {}) if isinstance(data, dict) else {}
        if isinstance(tab, dict):
            return (
                f"Browser: navigated tab={tab.get('id', '')} url={tab.get('url', '')}"
            )
    return f"Browser: {action or 'ok'}"


def _browser_status_payload(
    *,
    runtime_env: Mapping[str, object] | None = None,
    browser_config: Any | None = None,
    config_path: str | None = None,
) -> dict[str, Any]:
    registry = provider_registry()
    providers = registry.list_provider_ids()
    sidecar = _default_browser_sidecar_manager(
        config_path=config_path,
        runtime_env=runtime_env,
    ).status("pinchtab")
    env = resolve_tool_env(runtime_env=runtime_env)
    runtime_default = (
        str(getattr(browser_config, "default_provider", "") or "").strip()
        or env.get(OPENMINION_BROWSER_DEFAULT_PROVIDER_ENV, "").strip()
    )
    provider_order = tuple(getattr(browser_config, "provider_order", ()) or ())
    enabled_providers = tuple(getattr(browser_config, "enabled_providers", ()) or ())
    selected_provider = _selected_provider(
        registry=registry,
        runtime_default=runtime_default,
        provider_order=provider_order,
        enabled_providers=enabled_providers,
    )
    provider_status = {
        BROWSER_PROVIDER_PINCHTAB: {
            "ready": bool(sidecar.get("ready")),
            "reason": str(sidecar.get("readiness_reason") or "").strip(),
        }
    }
    if BROWSER_PROVIDER_PLAYWRIGHT in providers:
        readiness = registry.get(BROWSER_PROVIDER_PLAYWRIGHT).ensure_ready(
            BrowserProviderContext()
        )
        provider_status[BROWSER_PROVIDER_PLAYWRIGHT] = {
            "ready": bool(readiness.get("ok")),
            "reason": _playwright_readiness_reason(readiness),
        }
    selected_status = provider_status.get(
        selected_provider,
        {"ready": False, "reason": "provider is not registered"},
    )
    context: dict[str, str] = {}
    if selected_provider == BROWSER_PROVIDER_PLAYWRIGHT:
        provider = cast(
            PlaywrightProvider,
            registry.get(BROWSER_PROVIDER_PLAYWRIGHT),
        )
        context = {
            "locale": env.get(
                OPENMINION_BROWSER_PLAYWRIGHT_LOCALE_ENV,
                provider.config.locale,
            ),
            "timezone_id": env.get(
                OPENMINION_BROWSER_PLAYWRIGHT_TIMEZONE_ENV,
                provider.config.timezone_id,
            ),
        }
    return {
        "ok": True,
        "action": "status",
        "providers": providers,
        "selected_provider": selected_provider,
        "selected_ready": bool(selected_status["ready"]),
        "provider_status": provider_status,
        "context": context,
        "repair": str(selected_status["reason"]),
        "sidecar": sidecar,
    }


def _selected_provider(
    *,
    registry: Any,
    runtime_default: str,
    provider_order: tuple[str, ...],
    enabled_providers: tuple[str, ...],
) -> str:
    try:
        provider = BrowserRouter(registry).select_provider(
            requested_provider=None,
            agent_profile_provider=None,
            runtime_default_provider=runtime_default,
            runtime_provider_order=provider_order,
            runtime_enabled_providers=enabled_providers,
        )
    except KeyError:
        return runtime_default
    return str(provider.provider_id)


def _playwright_readiness_reason(payload: Mapping[str, Any]) -> str:
    error = payload.get("error")
    if not isinstance(error, Mapping):
        return ""
    remediation = error.get("remediation")
    if isinstance(remediation, list):
        return "; ".join(str(item) for item in remediation if str(item).strip())
    return str(error.get("message") or "").strip()


def _default_browser_sidecar_manager(
    *,
    config_path: str | None = None,
    runtime_env: Mapping[str, object] | None = None,
) -> Any:
    return default_sidecar_manager(
        config_path=config_path,
        runtime_env=cast(Mapping[str, str] | None, runtime_env),
    )


def _execute_browser_tool(
    payload: dict[str, Any],
    working_dir: str | None,
    *,
    runtime_env: Mapping[str, object] | None = None,
    browser_config: Any | None = None,
    session_id: str = "cli-browser",
    agent_id: str = "",
) -> dict[str, Any]:
    workspace_root = str(Path(working_dir or Path.cwd()).resolve())
    metadata: dict[str, Any] = {
        "workspace_root": workspace_root,
        "runtime_env": dict(runtime_env or {}),
    }
    if browser_config is not None:
        metadata.update(
            build_runtime_tool_routing_metadata({"browser": browser_config})
        )
    if agent_id:
        metadata["agent_id"] = agent_id
    tool = default_browser_tool()
    ctx = ToolExecutionContext(
        channel="cli",
        target="browser",
        session_id=str(session_id or "cli-browser"),
        metadata=cast(dict[str, str], metadata),
    )
    result = tool.execute(payload, ctx)
    if not result.ok:
        return {"ok": False, "action": _action_name(payload), "error": result.error}
    response = {"ok": True, "action": _action_name(payload), "data": result.data}
    if payload.get("op") == "tab.list":
        response["active_tab_id"] = _active_tab_id(
            tool,
            provider_id=str(result.data.get("provider") or ""),
            session_id=str(session_id or "cli-browser"),
            workspace_root=workspace_root,
            runtime_env=runtime_env,
        )
    return response


def _active_tab_id(
    tool: Any,
    *,
    provider_id: str,
    session_id: str,
    workspace_root: str,
    runtime_env: Mapping[str, object] | None,
) -> str:
    store = getattr(tool, "session_state_store", None)
    if store is None or not provider_id:
        return ""
    state = store.state_for(
        provider_id=provider_id,
        session_id=session_id,
        workspace_root=workspace_root,
        extras={},
        env=runtime_env,
    )
    return str(state.tab_id or "").strip()


def _runtime_browser_context(runtime: Any | None) -> dict[str, Any]:
    if runtime is None:
        return {}
    api_runtime = getattr(runtime, "api_runtime", None)
    config = getattr(api_runtime, "config", None)
    runtime_config = getattr(config, "runtime", None)
    agent_id = str(getattr(runtime, "agent_id", "") or "").strip()
    browser_config = getattr(getattr(runtime_config, "tools", None), "browser", None)
    if config is not None and getattr(config, "agents", None):
        browser_config = resolve_runtime_profile(
            config,
            agent_id=agent_id or None,
        ).tools.browser
    return {
        "runtime_env": getattr(runtime_config, "env", None),
        "browser_config": browser_config,
        "config_path": str(getattr(api_runtime, "config_path", "") or "") or None,
        "session_id": str(getattr(runtime, "session_id", "") or "cli-browser"),
        "agent_id": agent_id,
    }


def _format_tab_row(tab: Mapping[str, Any], *, active_tab_id: str) -> str:
    tab_id = str(tab.get("id", "") or "").strip()
    active = "[active] " if tab_id and tab_id == active_tab_id else ""
    title = str(tab.get("title", "") or "").strip() or "(untitled)"
    url = str(tab.get("url", "") or "").strip() or "(no URL)"
    return f"- {active}{tab_id} {title} {url}"


def _error_payload(message: str) -> dict[str, Any]:
    return {"ok": False, "error": str(message or "browser command failed")}


def _action_name(payload: dict[str, Any]) -> str:
    op = str(payload.get("op") or "").strip()
    if op == "tab.list":
        return "tabs"
    if op == "tab.navigate":
        return "navigate"
    if op == "instance.kill":
        return "stop"
    return op or "browser"


def _parse_options(tokens: list[str]) -> dict[str, str]:
    options: dict[str, str] = {}
    for token in tokens:
        if "=" in token:
            key, value = token.split("=", 1)
            options[key.strip().replace("-", "_")] = value.strip()
    return options


def _provider_args(options: dict[str, str]) -> dict[str, str]:
    provider = options.get("provider", "").strip()
    return {"provider": provider} if provider else {}


def _is_truthy(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}
