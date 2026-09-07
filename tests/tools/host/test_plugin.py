from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.registry import ToolRegistry
from openminion.modules.tool.runtime import RuntimeContext
from openminion.modules.tool.runtime.policy import Policy
from openminion.tools.host.plugin import (
    _h_inventory_report,
    _h_metrics,
    collect_host_metrics,
    register,
)


def _ctx(tmp_path: Path) -> RuntimeContext:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    return RuntimeContext(
        policy=Policy(
            raw={
                "workspace_root": str(workspace),
                "paths": {
                    "read_allow": [str(workspace)],
                    "write_allow": [str(workspace)],
                    "deny": [],
                },
                "commands": {"mode": "allowlist", "allow": []},
                "tools": {"allow_prefix": [""]},
            }
        ),
        workspace=workspace,
        run_root=tmp_path / "run",
        scope="READ_ONLY",
        confirm=False,
    )


def test_register_adds_host_metrics_tool() -> None:
    registry = ToolRegistry()
    register(registry)
    assert "host.metrics" in registry.list()
    assert "host.inventory_report" in registry.list()


def test_h_metrics_returns_platform_disk_and_memory(tmp_path: Path) -> None:
    payload = _h_metrics({}, _ctx(tmp_path))
    assert payload["ok"] is True
    assert payload["verified"] is True
    assert payload["data"]["method"] == "host.metrics"
    assert payload["data"]["platform"]["system"]
    assert payload["data"]["disk"]
    assert "Memory:" in payload["content"]


def test_collect_host_metrics_reuses_tool_owner_without_runtime_context(
    tmp_path: Path,
) -> None:
    payload, warnings = collect_host_metrics(tmp_path)

    assert payload["method"] == "host.metrics"
    assert payload["platform"]["system"]
    assert payload["disk"]
    assert "memory" in payload
    assert isinstance(warnings, list)


def test_h_metrics_accepts_relative_disk_path(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    workspace = Path(ctx.workspace)
    nested = workspace / "nested"
    nested.mkdir()

    payload = _h_metrics(
        {"path": "nested", "include_memory": False},
        ctx,
    )

    assert payload["ok"] is True
    paths = {item["path"] for item in payload["data"]["disk"]}
    assert str(nested) in paths
    assert "Memory:" not in payload["content"]


def test_h_metrics_content_formats_unknown_memory(monkeypatch, tmp_path: Path) -> None:
    def fake_memory() -> dict[str, Any]:
        return {
            "total_bytes": None,
            "available_bytes": None,
            "used_bytes": None,
            "used_percent": None,
            "source": "test",
        }

    monkeypatch.setattr("openminion.tools.host.plugin._memory_metrics", fake_memory)
    payload = _h_metrics({"include_disk": False}, _ctx(tmp_path))
    assert payload["ok"] is True
    assert "Memory: unknown used / unknown total" in payload["content"]


def test_h_inventory_report_writes_matching_json_and_markdown(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)

    payload = _h_inventory_report({"output_dir": "inventory"}, ctx)

    workspace = Path(ctx.workspace)
    json_path = workspace / payload["data"]["json_path"]
    markdown_path = workspace / payload["data"]["markdown_path"]
    report = json.loads(json_path.read_text(encoding="utf-8"))
    markdown = markdown_path.read_text(encoding="utf-8")
    assert payload["verified"] is True
    assert report["schema_version"] == "openminion.local-system-inventory.v1"
    assert list(report) == [
        "schema_version",
        "source",
        "platform",
        "memory",
        "disk",
        "warnings",
    ]
    assert markdown.startswith("# Local System Inventory\n")
    assert markdown.index("## Platform") < markdown.index("## Memory")
    assert markdown.index("## Memory") < markdown.index("## Disk")
    assert markdown.index("## Disk") < markdown.index("## Warnings")
    assert f"- system: {report['platform']['system']}" in markdown
    assert f"- total_bytes: {report['memory']['total_bytes']}" in markdown
    assert str(tmp_path) not in json_path.read_text(encoding="utf-8")
    assert payload["data"]["json_path"] == "inventory/system-inventory.json"
    assert payload["data"]["markdown_path"] == "inventory/system-inventory.md"
    for disk in report["disk"]:
        assert f"- path: {disk['path']}" in markdown


def test_h_inventory_report_does_not_overwrite_without_permission(
    tmp_path: Path,
) -> None:
    ctx = _ctx(tmp_path)
    _h_inventory_report({"output_dir": "inventory"}, ctx)

    with pytest.raises(ToolRuntimeError, match="already exists"):
        _h_inventory_report({"output_dir": "inventory"}, ctx)


def test_h_inventory_report_rejects_path_outside_workspace(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)

    with pytest.raises(ToolRuntimeError, match="escapes workspace root"):
        _h_inventory_report({"output_dir": "../outside"}, ctx)
