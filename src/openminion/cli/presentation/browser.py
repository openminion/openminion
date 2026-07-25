from __future__ import annotations

import shlex
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from openminion.services.runtime.sidecars import default_sidecar_manager
from openminion.tools.browser import default_browser_tool, provider_registry


def browser_command_payload(args: str, *, working_dir: str | None = None) -> dict[str, Any]:
    tokens = shlex.split(str(args or ""))
    action = tokens[0].lower() if tokens else "status"
    options = _parse_options(tokens[1:])
    if action == "status":
        return _browser_status_payload()
    if action == "tabs":
        return _execute_browser_tool({"op": "tab.list", **_provider_args(options)}, working_dir)
    if action == "navigate":
        url = options.get("url") or (tokens[1] if len(tokens) > 1 else "")
        if not url:
            return _error_payload("url is required for browser navigate")
        payload = {"op": "tab.navigate", "url": url, **_provider_args(options)}
        if options.get("tab"):
            payload["tab_id"] = options["tab"]
        return _execute_browser_tool(payload, working_dir)
    if action == "stop":
        if _is_truthy(options.get("sidecar", "1")):
            result = default_sidecar_manager().stop(
                name="pinchtab",
                kill=_is_truthy(options.get("kill", "0")),
            )
            return {"ok": True, "action": "stop", "sidecar": "pinchtab", "result": result}
        instance_id = options.get("instance") or options.get("instance_id")
        if not instance_id:
            return _error_payload("instance=<id> is required when sidecar=0")
        return _execute_browser_tool(
            {"op": "instance.kill", "instance_id": instance_id, **_provider_args(options)},
            working_dir,
        )
    return _error_payload("usage: /browser [status|tabs|navigate|stop]")


def render_browser_command(args: str, *, working_dir: str | None = None) -> str:
    payload = browser_command_payload(args, working_dir=working_dir)
    if not payload.get("ok"):
        return f"Browser: error: {payload.get('error', 'unknown error')}"
    action = str(payload.get("action") or "").strip()
    if action == "status":
        providers = ", ".join(payload.get("providers", [])) or "(none)"
        sidecar = payload.get("sidecar", {})
        sidecar_label = _sidecar_label(sidecar if isinstance(sidecar, dict) else {})
        return f"Browser: providers={providers} sidecar={sidecar_label}"
    if action == "stop":
        result = payload.get("result", {})
        stopped = bool(result.get("stopped")) if isinstance(result, dict) else False
        return f"Browser: pinchtab sidecar stop requested stopped={stopped}"
    data = payload.get("data", {})
    if action == "tabs":
        tabs = data.get("tabs", []) if isinstance(data, dict) else []
        rows = [
            f"- {tab.get('id', '')} {tab.get('title', '')} {tab.get('url', '')}"
            for tab in tabs
            if isinstance(tab, dict)
        ]
        return "Browser tabs:\n" + ("\n".join(rows) or "(none)")
    if action == "navigate":
        tab = data.get("tab", {}) if isinstance(data, dict) else {}
        if isinstance(tab, dict):
            return f"Browser: navigated tab={tab.get('id', '')} url={tab.get('url', '')}"
    return f"Browser: {action or 'ok'}"


def _browser_status_payload() -> dict[str, Any]:
    sidecar = default_sidecar_manager().status("pinchtab")
    return {
        "ok": True,
        "action": "status",
        "providers": provider_registry().list_provider_ids(),
        "sidecar": sidecar,
    }


def _execute_browser_tool(
    payload: dict[str, Any],
    working_dir: str | None,
) -> dict[str, Any]:
    ctx = SimpleNamespace(
        runtime=None,
        trace_id="",
        session_id="cli-browser",
        extras={"workspace_root": str(Path(working_dir or Path.cwd()).resolve())},
    )
    result = default_browser_tool().execute(payload, ctx)
    if not result.ok:
        return {"ok": False, "action": _action_name(payload), "error": result.error}
    return {"ok": True, "action": _action_name(payload), "data": result.data}


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


def _sidecar_label(sidecar: dict[str, Any]) -> str:
    if not sidecar:
        return "unknown"
    ready = sidecar.get("ready")
    if ready is not None:
        return "ready" if ready else f"not-ready:{sidecar.get('readiness_reason', '')}"
    return "alive" if sidecar.get("pid_alive") else "stopped"
