"""Public helper facade for the tool package."""

from __future__ import annotations

from typing import Any

from openminion.base.version import OPENMINION_VERSION

from .bootstrap import build_runtime_bootstrap
from .constants import (
    TOOL_BOOTSTRAP_STATUS_ALREADY_REGISTERED,
    TOOL_BOOTSTRAP_STATUS_REGISTERED,
)
from .registry import ToolRegistry

__version__ = OPENMINION_VERSION

# Module-first tool source gates. Wave-1 TMFC/WOMC migrations forced this path.
_MODULES_ONLY = True
_TAVILY_SOURCE = "module_first"
_WEATHER_SOURCE = "module_first"


def build_default_tool_registry(
    *,
    config: Any | None = None,
    workspace_root: Any | None = None,
    run_root: Any | None = None,
    strict: bool = False,
) -> ToolRegistry:
    bootstrap = build_runtime_bootstrap(
        config=config,
        workspace_root=workspace_root,
        run_root=run_root,
        strict=strict,
    )
    return bootstrap.registry


def build_default_tool_registry_debug_report() -> dict[str, Any]:
    bootstrap = build_runtime_bootstrap(
        config=None,
        workspace_root=None,
        run_root=None,
        strict=False,
    )
    records = [record.__dict__.copy() for record in (bootstrap.bootstrap_records or [])]
    required_failures = [
        record
        for record in records
        if bool(record.get("required"))
        and str(record.get("status") or "")
        not in {
            TOOL_BOOTSTRAP_STATUS_REGISTERED,
            TOOL_BOOTSTRAP_STATUS_ALREADY_REGISTERED,
        }
    ]
    return {
        "ok": len(required_failures) == 0,
        "required_failures": required_failures,
        "bootstrap_records": records,
        "registry_snapshot": bootstrap.registry.registration_debug_snapshot(),
    }
