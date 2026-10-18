from __future__ import annotations

import argparse
from typing import Literal, cast

from openminion.api.runtime import APIRuntime
from openminion.base.config.core import resolve_default_agent_id
from openminion.modules.llm.providers import ProviderToolCall
from openminion.modules.policy.adapters.composition import (
    SEAM_CLI_CRON,
    SEAM_CLI_TASKS,
    build_default_composition_boundary_adapter,
)
from openminion.modules.task.constants import DEFAULT_TASK_MIN_EVERY_MS
from openminion.modules.task.surface import resolve_task_surface_source
from openminion.modules.tool.base import ToolExecutionContext
from openminion.modules.tool.runtime.routing import build_runtime_tool_routing_metadata
from openminion.modules.tool.selection import ToolSelectionService


def build_schedule_payload(args: argparse.Namespace) -> dict[str, object]:
    every_ms = getattr(args, "every_ms", None)
    cron_expr = str(getattr(args, "cron_expr", "") or "").strip()
    at_iso = str(getattr(args, "at_iso", "") or "").strip()
    timezone = str(getattr(args, "timezone", "") or "").strip()

    if every_ms is not None:
        interval = int(every_ms)
        if interval < DEFAULT_TASK_MIN_EVERY_MS:
            raise ValueError(f"--every-ms must be at least {DEFAULT_TASK_MIN_EVERY_MS}")
        return {"kind": "every", "every_ms": interval}
    if cron_expr:
        schedule: dict[str, object] = {"kind": "cron", "expr": cron_expr}
        if timezone:
            schedule["tz"] = timezone
        return schedule
    if at_iso:
        return {"kind": "at", "at": at_iso}
    raise ValueError("one schedule selector is required: --every-ms, --cron, or --at")


def execute_task_tool(
    app: APIRuntime,
    args: argparse.Namespace,
    *,
    tool_name: str,
    payload: dict[str, object],
    owner: Literal["cron", "tasks"],
) -> dict[str, object]:
    runtime_config = app.config.runtime
    source = f"cli-{owner}"
    metadata: dict[str, object] = {
        "origin": f"openminion.{owner}",
        "tool_call_origin": "cli",
        "allow_runtime_direct": "1",
        "runtime_env": dict(getattr(runtime_config, "env", {}) or {}),
        **build_runtime_tool_routing_metadata(getattr(runtime_config, "tools", None)),
        **ToolSelectionService(
            getattr(runtime_config, "tool_selection", None),
            app.tools,
        ).runtime_binding_policy_metadata(),
    }
    agent_id = str(getattr(args, "agent_id", "") or "").strip()
    metadata["agent_id"] = agent_id or resolve_default_agent_id(app.config)
    session_id = str(getattr(args, "session", "") or "").strip() or owner
    batch = app.tools.execute_calls(
        [ProviderToolCall(name=tool_name, arguments=payload, source=source)],
        context=ToolExecutionContext(
            channel="console",
            target=source,
            session_id=session_id,
            metadata=cast(dict[str, str], metadata),
            task_manager=resolve_task_surface_source(app),
            scheduler_readiness=app.scheduler_readiness,
            confirm=bool(getattr(args, "confirm", False)),
            blast_radius_adapter=build_default_composition_boundary_adapter(
                seam_id=SEAM_CLI_CRON if owner == "cron" else SEAM_CLI_TASKS,
            ),
        ),
    )
    result = batch.results[0] if batch.results else None
    if result is None:
        return {
            "ok": False,
            "tool": tool_name,
            "error": f"{tool_name} produced no result",
            "data": {},
        }
    return {
        "ok": bool(result.ok),
        "tool": tool_name,
        "error": str(result.error or ""),
        "data": dict(result.data or {}),
    }
