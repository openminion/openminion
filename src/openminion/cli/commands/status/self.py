from __future__ import annotations

from typing import Any

from openminion.api.runtime import APIRuntime
from openminion.cli.presentation.json_output import print_json_payload

from .runtime import _load_runtime_surface_payload


def run_self_status(args, *, config) -> int:
    source, payload = _load_runtime_surface_payload(
        args=args,
        config=config,
        path="/v1/runtime/self-model",
        inproc_call=lambda: _build_inproc_self_model_payload(args.config),
    )
    self_model = dict(payload.get("self_model", {}) or {})
    output = {
        "ok": bool(payload.get("ok", False)),
        "source": source,
        "health": payload.get("health") or self_model.get("health", "unavailable"),
        "self_model": self_model,
    }
    if getattr(args, "json", False):
        print_json_payload(output)
        return 0 if output["ok"] else 1
    _print_self_status(output)
    return 0 if output["ok"] else 1


def _build_inproc_self_model_payload(config_path: str | None) -> dict[str, Any]:
    runtime = APIRuntime.from_config_path(config_path)
    try:
        snapshot = runtime.runtime_self_model()
        return {"ok": True, "self_model": snapshot, "health": snapshot.get("health")}
    finally:
        runtime.close()


def _print_self_status(payload: dict[str, Any]) -> None:
    model = dict(payload.get("self_model", {}) or {})
    identity = _section(model, "identity")
    capabilities = _section(model, "capabilities")
    memory = _section(model, "memory_state")
    context = _section(model, "context_state")
    improvement = _section(model, "improvement_state")
    degraded = list(model.get("degraded_reasons", []) or [])
    print(
        "status self: "
        f"source={payload.get('source', '')} "
        f"health={payload.get('health', 'unavailable')} "
        f"agent={model.get('agent_id', 'unknown')}"
    )
    print(
        "- identity: "
        f"name={identity.get('display_name', '-') or '-'} "
        f"mission={identity.get('mission', '-') or '-'}"
    )
    print(
        "- capabilities: "
        f"provider={capabilities.get('provider', '-') or '-'} "
        f"model={capabilities.get('model', '-') or '-'} "
        f"tools={capabilities.get('enabled_tool_count', 0)}/{capabilities.get('tool_count', 0)}"
    )
    print(
        "- memory: "
        f"provider={memory.get('provider', '-') or '-'} "
        f"provenance={memory.get('provenance_available', False)}"
    )
    print(
        "- context: "
        f"budget_total={context.get('budget_total', 0)} "
        f"compaction={context.get('compaction_state', '-') or '-'}"
    )
    print(
        "- improvement: "
        f"policy={improvement.get('policy', '-') or '-'} "
        f"posture={improvement.get('promotion_posture', '-') or '-'}"
    )
    if degraded:
        print("- degraded_reasons: " + ", ".join(str(item) for item in degraded))


def _section(model: dict[str, Any], name: str) -> dict[str, Any]:
    section = dict(model.get(name, {}) or {})
    return dict(section.get("facts", {}) or {})
